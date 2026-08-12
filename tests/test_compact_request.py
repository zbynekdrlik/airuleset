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
     GitHub / the issue). watchdog job 14 (compact_ticket_boundary) types
     `/compact` into that session's pane once it goes genuinely idle,
     reusing job 12's (model reconcile) exact idle guards.

     #400 (2026-08-12) — the ORIGINAL trigger for this, a Stop hook
     (notify-compact-request.sh) sniffing the turn's final message for a
     completed-ticket shape, is REMOVED: a bare `✅ DONE:` one-liner is
     indistinguishable from a genuine ticket boundary by text alone, and
     that hook's repeated re-fire on every ordinary turn is what let a
     stale request keep looking "fresh" for 11.2+ hours in a live
     incident. `notify-compact-request.sh` is now a PERMANENT NO-OP (kept
     registered as an inert placeholder — see its own header). Every
     `/compact` request now originates from a STRUCTURAL proof instead:
     the SubagentStop event hook (notify-compact-subagent-boundary.sh,
     origin=subagent-stop, reading the harness's own background_tasks
     registry directly) or a session's own explicit
     `compact-request --self` callback (origin=self-callback).
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
    # #122 (2026-07-28, REVERSED by #333) — used to let a request carrying
    # its OWN proof of a boundary (origin=="subagent-stop") bypass job 14's
    # busy-skip, on the premise that "a short send-keys reliably queues even
    # into a busy pane" (#65) made typing into a busy pane safe. #333's live
    # forensic trace (three same-day incidents on this box's own supervisor
    # session) proved the real hazard is not the TYPE, it is that a
    # busy-typed `/compact` sits QUEUED and only DRAINS (executes) at
    # whatever LATER turn's Stop is first genuinely ACCEPTED — under an
    # active `/goal` loop that is almost always either a real completion or
    # an ask-and-continue `❓`/`⏳`-blocked turn, exactly the boundary this
    # gate exists to refuse. So the exemption is gone: EVERY origin, proven
    # or not, now behaves like test_busy_pane_is_skipped_and_request_kept_
    # for_retry above.
    # ------------------------------------------------------------------- #

    def test_busy_pane_with_proven_boundary_origin_is_also_skipped(self):
        tmux, logs, path, _ = self._go(CB_BUSY_CAP, origin="subagent-stop")
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip busy" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_busy_pane_with_proven_boundary_origin_never_sets_the_shared_claim(self):
        # #333 -- since the busy skip above fires before any send is even
        # attempted, the shared claim (#78/#82) must never be touched.
        with m.patch.object(wd, "compact_claim_set") as claim_mock:
            self._go(CB_BUSY_CAP, origin="subagent-stop")
        self.assertFalse(claim_mock.called)

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


def _write_human_transcript(base, cwd, sid, ts_epoch,
                            text="text ktorý napísal používateľ"):
    """Write a minimal real transcript at <base>/<encoded-cwd>/<sid>.jsonl
    containing a single top-level `type=="user"` entry with genuinely
    HUMAN-typed content at `ts_epoch` -- #377's `_compact_recent_human_
    activity` gate needs a REAL transcript for `_last_human_prompt_ts` to
    read a human prompt timestamp from (one primitive layer below
    `_write_marker_transcript`, which only ever writes the LAST-turn status
    marker `transcript_last_marker` reads)."""
    from datetime import datetime, timezone
    d = Path(base) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    iso = datetime.fromtimestamp(ts_epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    entry = {"type": "user", "timestamp": iso, "message": {"content": text}}
    p.write_text(json.dumps(entry) + "\n")
    return p


def _touch_active_marker(testcase, sid, age=0):
    """Stamp `/tmp/claude-user-active-<sid>` (the UserPromptSubmit presence
    marker) at `age` seconds old (0 = now), same shape
    `tests/test_goal_autoarm.py`'s own `_touch_active` already uses for the
    IDENTICAL marker -- #377's new compact gate consults it too. Cleaned up
    on test teardown."""
    f = "/tmp/claude-user-active-%s" % sid
    Path(f).write_text("")
    if age:
        old = time.time() - age
        os.utime(f, (old, old))
    testcase.addCleanup(lambda: os.path.exists(f) and os.remove(f))
    return f


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
        # 250_000 since 2026-08-08 (user directive — post-compact baseline of a
        # supervisor session already exceeds 200K, so the old floor was inert)
        self.assertEqual(wd.COMPACT_BOUNDARY_MIN_CONTEXT, 250_000)

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
        tmux, logs, reqpath = self._go(250_000)
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
# #400 update: `notify-compact-request.sh` (the record-time gate this
# comment originally named) is now a PERMANENT NO-OP -- it never records
# anything, so it cannot be the thing refusing a `❓`-ending turn any more.
# The reasoning below is unchanged regardless of WHICH origin recorded the
# request: a request recorded for an earlier ✅ boundary can still be
# sitting in compact-requests.json once the session has since moved on to
# a NEW `❓` turn (a `/compact` queued behind a
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
# 2b-2. #377 (2026-08-11 live evidence, gk) — never deliver `/compact` while
# the user is ACTIVELY ENGAGING with this session right now. Reuses job 9's
# own `_goal_autoarm_recent_human_activity` dual-signal primitive (the
# `/tmp/claude-user-active-<sid>` presence marker OR the transcript's own
# `_last_human_prompt_ts`), just with compact's own much shorter window --
# see `_compact_recent_human_activity`'s own docstring for the full
# reasoning.
# --------------------------------------------------------------------------- #

class TestCompactRecentHumanActivityGate(unittest.TestCase):
    SID = "sess-human-1"
    CWD = "/home/x/humanproj"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_no_transcript_no_marker_is_not_blocked(self):
        proj = self._dir()   # nothing written -> unmeasurable, never blocks
        now = time.time()
        self.assertFalse(
            wd._compact_recent_human_activity(self.CWD, self.SID, now,
                                              projects_dir=proj))

    def test_recent_presence_marker_blocks(self):
        proj = self._dir()
        now = time.time()
        _touch_active_marker(self, self.SID, age=5)
        self.assertTrue(
            wd._compact_recent_human_activity(self.CWD, self.SID, now,
                                              projects_dir=proj))

    def test_recent_transcript_human_prompt_blocks(self):
        proj = self._dir()
        now = time.time()
        _write_human_transcript(proj, self.CWD, self.SID, now - 10)
        self.assertTrue(
            wd._compact_recent_human_activity(self.CWD, self.SID, now,
                                              projects_dir=proj))

    def test_stale_marker_and_stale_prompt_does_not_block(self):
        proj = self._dir()
        now = time.time()
        win = wd.COMPACT_RECENT_HUMAN_ACTIVITY_S
        _write_human_transcript(proj, self.CWD, self.SID, now - win - 300)
        _touch_active_marker(self, self.SID, age=win + 300)
        self.assertFalse(
            wd._compact_recent_human_activity(self.CWD, self.SID, now,
                                              projects_dir=proj))

    def test_a_stop_hook_feedback_entry_is_never_read_as_human(self):
        # the SAME machine-injected-prompt exclusion job 9's gate already
        # relies on (`_MACHINE_PROMPT_PREFIXES`) -- a routine Stop-hook
        # rejection must never delay a genuinely-idle compact.
        proj = self._dir()
        now = time.time()
        _write_human_transcript(
            proj, self.CWD, self.SID, now - 5,
            text="Stop hook feedback: [some-hook.sh] blocked this turn")
        self.assertFalse(
            wd._compact_recent_human_activity(self.CWD, self.SID, now,
                                              projects_dir=proj))

    def test_explicit_window_s_overrides_the_default(self):
        proj = self._dir()
        now = time.time()
        _write_human_transcript(proj, self.CWD, self.SID, now - 500)
        # far outside the default window, but INSIDE a widened one passed
        # explicitly -- proves window_s is genuinely threaded through, not
        # just accepted and ignored.
        self.assertFalse(
            wd._compact_recent_human_activity(self.CWD, self.SID, now,
                                              projects_dir=proj))
        self.assertTrue(
            wd._compact_recent_human_activity(self.CWD, self.SID, now,
                                              projects_dir=proj, window_s=600))

    def test_a_discord_relayed_answer_counts_as_recent_human_activity(self):
        # #377-review MINOR-1 (fresh-context adversarial review) -- the
        # incident's own reported shape IS a Discord-relayed answer, which
        # `_last_human_prompt_ts` deliberately excludes from ITS "human
        # typed directly" question (`_MACHINE_PROMPT_PREFIXES`, #339). The
        # compact veto's own question is different: "is the user actively
        # engaging right now" -- a Discord answer genuinely is, mirroring
        # #350's own established "opposite exclusion set" precedent for
        # the identical two prefixes. Must count even with NO presence
        # marker at all (job 7's delivery never stamps that marker).
        proj = self._dir()
        now = time.time()
        _write_human_transcript(proj, self.CWD, self.SID, now - 5,
                                text="Odpoveď z Discordu: áno, pokračuj")
        self.assertTrue(
            wd._compact_recent_human_activity(self.CWD, self.SID, now,
                                              projects_dir=proj))

    def test_the_other_discord_relay_prefix_also_counts(self):
        proj = self._dir()
        now = time.time()
        _write_human_transcript(
            proj, self.CWD, self.SID, now - 5,
            text="Odpoveď užívateľa na tvoju otázku: nie, zruš to")
        self.assertTrue(
            wd._compact_recent_human_activity(self.CWD, self.SID, now,
                                              projects_dir=proj))


class TestCompactRecentHumanWindowClamp(unittest.TestCase):
    """#377-review MINOR-2/3 (fresh-context adversarial review, executed
    proof) -- `_compact_recent_human_window`'s env/const-derived default
    was UNCLAMPED: `AIRULESET_COMPACT_RECENT_HUMAN_S=0` silently disabled
    the veto outright, and a value >= `COMPACT_REQUEST_MAX_AGE_S` recreated
    the exact lapse-before-clear starvation `_compact_defer_grace`'s own
    clamp already exists to prevent for its sibling window. Mirrors that
    function's own test shape exactly (`TestCompactDeferGraceRelationship`
    et al., above)."""

    def test_env_override_negative_is_clamped_to_a_minimum(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_RECENT_HUMAN_S": "-100"}):
            self.assertEqual(wd._compact_recent_human_window(), 1)

    def test_env_override_zero_is_clamped_to_a_minimum(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_RECENT_HUMAN_S": "0"}):
            self.assertEqual(wd._compact_recent_human_window(), 1)

    def test_env_override_non_numeric_falls_back_to_the_default(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_RECENT_HUMAN_S": "abc"}):
            self.assertEqual(wd._compact_recent_human_window(),
                             wd.COMPACT_RECENT_HUMAN_ACTIVITY_S)

    def test_env_override_at_or_above_ttl_is_clamped_below_it(self):
        huge = str(wd.COMPACT_REQUEST_MAX_AGE_S + 1000)
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_RECENT_HUMAN_S": huge}):
            self.assertLess(wd._compact_recent_human_window(),
                            wd.COMPACT_REQUEST_MAX_AGE_S)

    def test_env_override_exactly_at_ttl_is_clamped_below_it(self):
        at_ttl = str(wd.COMPACT_REQUEST_MAX_AGE_S)
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_RECENT_HUMAN_S": at_ttl}):
            self.assertLess(wd._compact_recent_human_window(),
                            wd.COMPACT_REQUEST_MAX_AGE_S)

    def test_a_sane_env_override_is_returned_unclamped(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_RECENT_HUMAN_S": "60"}):
            self.assertEqual(wd._compact_recent_human_window(), 60)

    def test_explicit_window_s_param_is_never_clamped(self):
        # every production call leaves window_s=None; an explicit override
        # (test/caller-only) is returned verbatim, even outside the sane
        # range -- the clamp protects only the env/const-derived default.
        self.assertEqual(wd._compact_recent_human_window(window_s=-5), -5)
        self.assertEqual(
            wd._compact_recent_human_window(
                window_s=wd.COMPACT_REQUEST_MAX_AGE_S + 5),
            wd.COMPACT_REQUEST_MAX_AGE_S + 5)


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
    def __init__(self, panes, captured, in_mode=False, cap_seq=()):
        self.panes = panes          # [(pane_id, cmd, cwd, pid)]
        self.captured = captured
        self.in_mode = in_mode
        # #333-review MAJOR-2 -- same shape as CompactFakeTmux's own
        # cap_seq: an EMPTY sequence (every pre-#333 test) preserves the old
        # fixed-`self.captured` behavior for every capture-pane call, incl.
        # this function's own top-of-call resolve. A non-empty sequence lets
        # a test simulate the pane having MOVED ON by the time of the
        # fresh, pre-send re-capture #333 added -- each real capture-pane
        # call (the initial resolve, then the pre-send re-verify) consumes
        # the next entry.
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

    def test_busy_pane_now_falls_back_REVERSES_65(self):
        # #333 REVERSES #65's own premise: a short send-keys DOES queue
        # reliably even into a busy pane, but the queued `/compact` then
        # only DRAINS (executes) at whatever LATER turn's Stop is first
        # accepted — under an active `/goal` loop that is almost always a
        # real completion or a `❓`/`⏳`-blocked turn, exactly the boundary
        # this whole gate exists to refuse. A busy pane is now a reason to
        # fall back to job 14's polled retry, same as a genuine draft.
        ok, tmux = self._go(CB_BUSY_CAP)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])

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
# #301 (2026-08-07) — job 14's own #99/#48 gates never received `proven_boundary`
# at all: #126 exempted a PROVEN boundary (origin in
# `_COMPACT_PROVEN_BOUNDARY_ORIGINS` — `subagent-stop`/`self-callback`) from
# BOTH substantiality heuristics in `deliver_compact_now` (the synchronous
# path), but its own docstring says plainly "job 14's own separate copy of
# these same two gates is untouched" — a KNOWN, deliberately-scoped-out parity
# gap, not an oversight (#122's docstring: "#126 already scoped that parity
# gap out explicitly ... this ticket does not fold it back in").
#
# Live evidence on gk + david@subdev (both boxes running #250's own grace
# fix): `compact-decisions.log` shows the OVERWHELMING majority of RECORD
# lines are `type=autopilot-worker` (origin=subagent-stop, #121's own proof
# of a completed-ticket boundary) — and the very NEXT thing that happens to
# most of them, once they fall through to job 14's poll, is
# `skip no-work (compact-request)` / `DROP no-work` even though origin
# already PROVES the boundary. On gk: `journalctl` job-14 tally over 3 days
# showed `skip no-work` as the SECOND most common outcome (53 of 213), and
# `compact-sync.log` shows a real multi-hour stretch (18h35m on
# david@subdev, 2026-08-06T18:38:10 -> 2026-08-07T13:13:15) with ZERO
# compact-request activity of any kind because every recorded request in
# that window was PROVEN (origin=subagent-stop) yet still evaluated against
# the unconditional #99 gate and dropped/lapsed.
#
# THE FIX: extend job 14's ALREADY-COMPUTED `proven_boundary` flag (it
# already exists a few lines above, used for the `kind == "busy"` check —
# NOT for thin-context, which never consults it at all, see below) to also
# exempt the #99 no-work and #48 small-context gates — the EXACT
# same exemption `deliver_compact_now` already applies, closing the parity
# gap #122/#126 left open on purpose. `_compact_thin_context` stays
# UNCONDITIONAL for both paths (per its own section comment — the live
# incident it exists for WAS origin=subagent-stop); only #99/#48 gain the
# exemption, mirroring `deliver_compact_now` exactly.
# --------------------------------------------------------------------------- #

class TestSubagentStopOriginExempt_FromSubstantialityGates_InJob14(unittest.TestCase):
    """The polled path (job 14) must give a PROVEN boundary (origin in
    `_COMPACT_PROVEN_BOUNDARY_ORIGINS`) the SAME exemption from #99/#48 that
    `deliver_compact_now` already grants it (#126) — before this fix, job 14
    silently dropped a proven boundary the synchronous path had already
    deferred, reproducing the exact starvation #301 was filed to investigate."""
    SID = "sess-subst-job14-proven"
    CWD = "/home/x/subst-job14-proven-proj"
    PANE = "%9"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, substantial, ctx_tokens=300_000, origin="subagent-stop"):
        base = self._dir()
        proj = base / "projects"
        reqpath = base / "compact-requests.json"
        _write_ctx_transcript(proj, self.CWD, self.SID, ctx_tokens)
        wd.record_compact_request(self.SID, self.CWD, path=reqpath, origin=origin)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        panes_by_sid = {self.SID: (self.PANE, CB_IDLE_CAP)}
        with m.patch.object(wd, "compact_boundary_substantial",
                           return_value=substantial):
            logs = wd.compact_ticket_boundary(
                time.time(), tmux, {}, panes_by_sid, path=reqpath,
                projects_dir=proj)
        return tmux, logs, reqpath

    # -- POSITIVE controls: a proven subagent-stop boundary now SENDS ---- #

    def test_no_work_no_longer_vetoes_a_proven_boundary(self):
        tmux, logs, reqpath = self._go(substantial=False, ctx_tokens=300_000)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertFalse(any("skip no-work" in ln for ln in logs), logs)

    def test_small_context_no_longer_vetoes_a_proven_boundary(self):
        tmux, logs, reqpath = self._go(substantial=True, ctx_tokens=1_000)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertFalse(any("skip small-context" in ln for ln in logs), logs)

    def test_both_gates_together_no_longer_veto_a_proven_boundary(self):
        tmux, logs, reqpath = self._go(substantial=False, ctx_tokens=1_000)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)

    def test_self_callback_origin_is_also_exempt(self):
        tmux, logs, reqpath = self._go(substantial=False, ctx_tokens=300_000,
                                       origin="self-callback")
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)

    # -- NEGATIVE controls: a blank/plain origin keeps the OLD behavior -- #

    def test_no_work_still_vetoes_a_plain_stop_hook_boundary(self):
        tmux, logs, reqpath = self._go(substantial=False, ctx_tokens=300_000,
                                       origin="")
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip no-work" in ln for ln in logs), logs)
        self.assertNotIn(self.SID, wd.load_compact_requests(reqpath))

    def test_small_context_still_vetoes_a_plain_stop_hook_boundary(self):
        tmux, logs, reqpath = self._go(substantial=True, ctx_tokens=1_000,
                                       origin="")
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip small-context" in ln for ln in logs), logs)


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
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
             m.patch("watchdog.deliver_compact_now", return_value=False):
            airuleset.cmd_compact_request(Args())
        reqfile = fake_home / ".claude" / "compact-requests.json"
        self.assertTrue(reqfile.exists())
        d2 = json.loads(reqfile.read_text())
        self.assertIn("sid-cli", d2)
        self.assertEqual(d2["sid-cli"]["cwd"], "/x")

    def test_immediate_delivery_success_clears_the_just_recorded_request(self):
        # #238-review 🔴1 -- `--record` now goes through `deliver_compact_record`
        # (a bounded retry over `deliver_compact_now`), not a single bare call
        # sharing ONE `time.time()` read for both `request_ts` and `now`. Pin
        # `time.time` so the record call and the retry's own fresh `now_fn()`
        # read the SAME instant deterministically (no real elapsed time to
        # cause a spurious inequality) -- this still proves the two are
        # threaded from the SAME clock, just no longer the same single read.
        fake_home = self._home()
        fixed_ts = 1_700_000_500.0
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
             m.patch("time.time", return_value=fixed_ts), \
             m.patch("watchdog.deliver_compact_now", return_value=True) as dcn:
            airuleset.cmd_compact_request(Args())
        # #121 — the request's own boundary PROOF is threaded to the sender on
        # every call; a blank origin (this Stop-hook-shaped caller) keeps
        # #109's gate exactly as it was.
        dcn.assert_called_once()
        call_args, call_kwargs = dcn.call_args
        self.assertEqual(call_args, ("sid-cli", "/x"))
        self.assertEqual(call_kwargs.get("origin"), "")
        # #250 — `request_ts`/`now` both carry the SAME wall-clock value this
        # call's own `record_compact_request` just wrote as `ts` — the
        # live-tasks grace check downstream measures age from THIS request,
        # not a freshly-recomputed timestamp.
        self.assertIn("request_ts", call_kwargs)
        self.assertEqual(call_kwargs.get("request_ts"), call_kwargs.get("now"))
        reqfile = fake_home / ".claude" / "compact-requests.json"
        d2 = json.loads(reqfile.read_text())
        self.assertNotIn("sid-cli", d2)

    def test_record_and_delivery_share_the_exact_same_ts(self):
        # #250 — the request FILE's own `ts` (what record_compact_request
        # wrote) and the `request_ts` handed to deliver_compact_now must be
        # the IDENTICAL value, not two separate `time.time()` reads a few
        # instructions apart.
        fake_home = self._home()
        fixed_ts = 1_700_000_000.0
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
             m.patch("time.time", return_value=fixed_ts), \
             m.patch("watchdog.deliver_compact_now", return_value=False) as dcn:
            airuleset.cmd_compact_request(Args())
        self.assertEqual(dcn.call_args.kwargs.get("request_ts"), fixed_ts)
        self.assertEqual(dcn.call_args.kwargs.get("now"), fixed_ts)
        reqfile = fake_home / ".claude" / "compact-requests.json"
        d2 = json.loads(reqfile.read_text())
        self.assertEqual(d2["sid-cli"]["ts"], int(fixed_ts))

    def test_immediate_delivery_failure_leaves_the_request_recorded(self):
        fake_home = self._home()
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
             m.patch("watchdog.deliver_compact_now", return_value=False):
            airuleset.cmd_compact_request(Args())
        reqfile = fake_home / ".claude" / "compact-requests.json"
        d2 = json.loads(reqfile.read_text())
        self.assertIn("sid-cli", d2)

    def test_immediate_delivery_exception_is_swallowed_and_request_stays_recorded(self):
        fake_home = self._home()
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
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
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
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
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
             m.patch("watchdog.deliver_compact_now", return_value=True) as dcn:
            airuleset.cmd_compact_request(a1)
            airuleset.cmd_compact_request(a2)
        self.assertEqual(dcn.call_count, 2)

    def test_failed_delivery_is_not_marked_so_a_retry_with_same_hash_still_tries(self):
        fake_home = self._home()
        a = Args()
        a.msg_hash = "fail-hash"
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
             m.patch("watchdog.deliver_compact_now", return_value=False):
            airuleset.cmd_compact_request(a)   # fails -> must NOT be marked
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
             m.patch("watchdog.deliver_compact_now", return_value=True) as dcn:
            airuleset.cmd_compact_request(a)   # same hash -> still tries
        dcn.assert_called_once()

    def test_blank_msg_hash_never_dedupes_pre_71_callers(self):
        # Args() with no msg_hash attribute at all (the pre-#71 shape) must
        # behave exactly as before -- every call attempts delivery.
        fake_home = self._home()
        with m.patch.dict(os.environ, {"HOME": str(fake_home), "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}), \
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
# 2e. notify-compact-request.sh — the Stop hook, now a PERMANENT NO-OP (#400)
# --------------------------------------------------------------------------- #

class TestCompactRequestHook(unittest.TestCase):
    """#400 (2026-08-12) — this hook is now a PERMANENT NO-OP: the passive
    text-sniffing `/compact` channel is removed entirely, in both
    directions. Every test below proves TEXT ALONE (whatever the message
    says, however shaped) never records anything, ever — the exact
    opposite of what this class asserted before #400. The only two
    surviving origins (`compact-request --self`,
    `notify-compact-subagent-boundary.sh`'s SubagentStop event hook) are
    covered by their own dedicated test classes, not this one."""

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

    def test_work_complete_heading_never_records(self):
        # #400 — inverted: this exact message used to record a request
        # (it is a genuine `## ✅ Work Complete` heading followed by a
        # terminal `✅ DONE:`). It must now do NOTHING.
        r, reqfile = self._run(
            "sid-1", "## ✅ Work Complete\n\nfoo bar\n✅ DONE: hotovo", cwd="/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_terminal_done_marker_alone_never_records(self):
        # #400 — inverted: a bare terminal `✅ DONE:` with no heading used
        # to record too (this is the EXACT trigger shape whose repeated
        # refresh let a stale request keep looking "fresh" for 11.2+ hours
        # in the live incident #400 responds to). Must now do NOTHING.
        r, reqfile = self._run("sid-2", "no heading here\n✅ DONE: hotovo", cwd="/y")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_blocked_on_user_never_records_even_with_the_heading(self):
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
        self.assertFalse(reqfile.exists())

    def test_wired_into_stop_hooks_json(self):
        # #400 — the file + its Stop registration are KEPT (a
        # permanently-neutered placeholder, not a silent removal from the
        # chain) — see the hook's own header for why.
        cfg = airuleset.load_hooks_json()
        cmds = [h.get("command", "")
               for entry in cfg.get("hooks", {}).get("Stop", [])
               for h in entry.get("hooks", [])]
        self.assertTrue(any("notify-compact-request.sh" in c for c in cmds), cmds)

    def test_no_decision_log_line_either(self):
        # #400 — the pre-#400 hook also appended a RECORD/type=stop-hook
        # line to ~/.claude/compact-decisions.log on every fire (#125).
        # That whole write path is gone too — no compact-decisions.log at
        # all is created by this hook any more.
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        payload = json.dumps({"session_id": "sid-nolog",
                              "last_assistant_message":
                              "## ✅ Work Complete\n✅ DONE: x", "cwd": "/z"})
        env = {**os.environ, "HOME": home}
        r = subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                           capture_output=True, env=env,
                           cwd=str(airuleset.REPO_DIR))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((Path(home) / ".claude" / "compact-decisions.log")
                         .exists())

    def test_arbitrarily_large_message_still_never_records(self):
        # #400 — the pre-#400 hook had argv-size-hazard hardening for a
        # huge message elsewhere in this repo's own hook family. This hook
        # no longer parses the message AT ALL, so a message well past any
        # prior size concern is simply discarded like every other one —
        # no crash, no record, exit 0.
        big = "## ✅ Work Complete\n" + ("x" * 200_000) + "\n✅ DONE: hotovo"
        r, reqfile = self._run("sid-big", big, cwd="/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())


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

    def _call(self, proj, captured=CB_BUSY_CAP):
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured)
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
        # #333 -- this test is about the REJECTED-vs-ACCEPTED stop gate
        # specifically, so it needs an IDLE capture (CB_BUSY_CAP would now
        # correctly refuse for the SEPARATE, unconditional busy-skip reason
        # #333 added -- see TestBusyPaneNoLongerDelivers -- which would
        # confound this test's own claim).
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        handled, tmux = self._call(proj, captured=CB_IDLE_CAP)
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
        #
        # #333 -- uses CB_IDLE_CAP (not CB_BUSY_CAP): this test's claim is
        # specifically about the rejected-stop gate, and a busy capture
        # would now ALSO be refused for the separate, unconditional
        # busy-skip #333 added -- confounding which gate the assertion is
        # actually proving passed.
        proj = self._proj()
        _write_rejected_boundary_transcript(proj, self.CWD, self.SID)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
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


# --------------------------------------------------------------------------- #
# #377 (2026-08-11 live evidence, gk) — the delivery-time veto for "the user
# just replied to a question": NEITHER job 14 NOR the synchronous path may
# type `/compact` while a recent human prompt/presence marker exists for
# this session, unconditionally (no origin exemption) — see
# `_compact_recent_human_activity`'s own section comment.
# --------------------------------------------------------------------------- #

class TestCompactRecentHumanActivityBlocksDelivery(unittest.TestCase):
    SID = "sess-recent-human-delivery"
    CWD = "/home/x/recenthumanproj"
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

    def test_job14_does_not_deliver_with_a_recent_presence_marker(self):
        proj = self._proj()
        # a real ✅ DONE boundary -- would deliver on its own (see the
        # positive control below); only the presence marker should block it.
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        _touch_active_marker(self, self.SID, age=5)
        path = self._p()
        now = time.time()
        wd.record_compact_request(self.SID, self.CWD, now=now, path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(now, tmux, {},
                                          {self.SID: (self.PANE, CB_IDLE_CAP)},
                                          path=path, projects_dir=proj)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("recent-human" in ln for ln in logs), logs)
        # left in place, never consumed -- the next sweep retries
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_job14_still_delivers_with_no_recent_human_activity(self):
        # positive control -- proves the new gate does not regress the
        # existing, already-working ✅-boundary delivery path.
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        path = self._p()
        now = time.time()
        wd.record_compact_request(self.SID, self.CWD, now=now, path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(now, tmux, {},
                                   {self.SID: (self.PANE, CB_IDLE_CAP)},
                                   path=path, projects_dir=proj)
        self.assertIn("/compact", tmux.typed_texts())

    def test_sync_path_does_not_deliver_with_a_recent_transcript_human_prompt(self):
        proj = self._proj()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo",
                                 ctx_tokens=300_000)
        # a HUMAN entry newer than the marker entry -- proves
        # `_last_human_prompt_ts` (not just `transcript_last_marker`) is
        # actually consulted by the new gate.
        d = Path(proj) / wd.encode_project_dir(self.CWD)
        p = d / (self.SID + ".jsonl")
        from datetime import datetime, timezone
        iso = datetime.fromtimestamp(now - 5, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "timestamp": iso,
                                "message": {"content": "odpoveď na otázku"}})
                    + "\n")
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1,
                                         now=now)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(handled)

    def test_sync_path_still_delivers_with_no_recent_human_activity(self):
        proj = self._proj()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo",
                                 ctx_tokens=300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1,
                                         now=now)
        self.assertEqual(handled, "sent")
        self.assertIn("/compact", tmux.typed_texts())


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
        # produce a LAPSE record.
        #
        # #400 FIX 6 (inverted from the pre-FIX-6 assertion that the sync
        # log stayed untouched by a real delivery) — job 14's own send
        # paths now ALSO write to compact-sync.log (they used to be
        # completely silent to it, the exact gap that made a live
        # gatekeeper /compact-fired-mid-work incident undebuggable). A
        # genuine delivery — CB_IDLE_CAP has no live-tasks row, so this
        # request really is sent — now DOES create the file, with a SEND
        # line naming this job as the sender.
        self.assertIn("/compact", tmux.typed_texts(), logs)
        sync_log = wd.compact_sync_log_path()
        self.assertTrue(Path(sync_log).exists())
        log_text = Path(sync_log).read_text()
        self.assertIn("SEND sid=%s" % self.SID, log_text)
        self.assertIn("via=job14", log_text)

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


class TestLogCompactSyncDedup(unittest.TestCase):
    """#238-review-style finding 🔵F6 (this ticket's own review, proven
    live) -- a bounded-retry caller re-invoking `deliver_compact_now`
    several times for the SAME sid/cwd can hit the SAME early-return
    decision on every attempt; `_log_compact_sync` must collapse a run of
    IDENTICAL consecutive lines into one (timestamp refreshed), never
    duplicate them into the bounded log."""

    def setUp(self):
        _isolate_compact_claims(self)

    def test_consecutive_identical_lines_collapse_to_one(self):
        for _ in range(5):
            wd._log_compact_sync("SKIP blocked-question sid=abc cwd=/x")
        text = wd.compact_sync_log_path().read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("SKIP blocked-question sid=abc cwd=/x", lines[0])

    def test_a_genuinely_different_line_is_appended_not_collapsed(self):
        wd._log_compact_sync("SKIP blocked-question sid=abc cwd=/x")
        wd._log_compact_sync("SKIP blocked-question sid=abc cwd=/x")
        wd._log_compact_sync("SEND sid=abc cwd=/x")
        text = wd.compact_sync_log_path().read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("SKIP blocked-question", lines[0])
        self.assertIn("SEND", lines[1])

    def test_the_refreshed_line_carries_the_latest_timestamp(self):
        wd._log_compact_sync("SKIP blocked-question sid=abc cwd=/x")
        first_text = wd.compact_sync_log_path().read_text(encoding="utf-8")
        wd._log_compact_sync("SKIP blocked-question sid=abc cwd=/x")
        second_text = wd.compact_sync_log_path().read_text(encoding="utf-8")
        self.assertEqual(len(second_text.splitlines()), 1)
        self.assertNotEqual(first_text, second_text,
                            "the timestamp must refresh even though the "
                            "line count stays the same")


class TestCompactHookRunsAfterTheStopGates(unittest.TestCase):
    """#109 (historical) — the enqueue-time gate could only SEE a rejection
    that an earlier hook had already produced, so `notify-compact-
    request.sh` had to stay ordered AFTER every `stop-check-*.sh` gate in
    the managed Stop chain. #400: the hook is now a permanent no-op with
    no enqueue-time gate left to protect -- this test is kept as a
    harmless ordering lock on the registered-but-inert placeholder, not
    because the ordering still matters functionally."""

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
    the moment an `autopilot-worker` concludes. `background_tasks` is this
    session's own live-task registry; a non-self entry (`id != agent_id`,
    #246) no longer prevents the record — it is a live-tasks DEFERRAL fact
    carried forward for the two DELIVERY-time gates
    (`_session_has_live_bg_tasks`, watchdog/__init__.py) to act on instead.
    Only an UNPROVABLE registry (absent/null/malformed/no ids) still
    prevents recording outright — see the module below."""

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
        env = {**os.environ, "HOME": home, "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}
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

    def test_another_live_worker_of_this_session_still_records(self):
        # #246: a sibling worker still running is no longer an outright
        # DECLINE here — the boundary is recorded like any other, with its
        # own PROVEN origin, and the live-tasks SAFETY property is enforced
        # at DELIVERY time instead (`_session_has_live_bg_tasks`, watchdog/
        # __init__.py) so the compact-stall backstop (job 26) always has an
        # artifact to watch.
        r, reqfile = self._run(sid="sup-c", tasks="sibling")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(reqfile.exists())
        d = json.loads(reqfile.read_text())
        self.assertEqual(d["sup-c"].get("origin"), _PROVEN)

    def test_a_pending_task_also_still_records(self):
        r, reqfile = self._run(sid="sup-d", tasks="pending-sibling")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(reqfile.exists())

    def test_a_live_shell_task_of_this_session_also_still_records(self):
        r, reqfile = self._run(sid="sup-e", tasks="shell")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(reqfile.exists())

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
    """#121 (2026-07-28) shipped this class's own original premise: a
    supervisor's `⏳` refers to the NEXT batch, never to the ticket that just
    landed, so it must not hold a request whose own origin already proved
    the boundary.

    #333 (2026-08-08) REVERSES that premise with live forensic evidence:
    this box's own transcript showed `/compact` typed while BUSY, sitting
    QUEUED, and only draining several turns later at whatever turn's Stop
    was first ACCEPTED — under an active `/goal` loop that is almost always
    either a genuine completion or an ask-and-continue `❓`/`⏳`-blocked
    turn. Both of this box's own confirmed-clean historical sends landed on
    a literal `✅ DONE` turn, never on `⏳`. So `⏳` now blocks delivery for
    EVERY origin, proven or not — the class name is kept (searchability for
    the #121→#333 history) but every "proven boundary bypasses ⏳" test
    below is INVERTED. #102's `❓` gate (`_compact_blocked_by_question`,
    which runs first at both send points and was NEVER relaxed by #121 in
    the first place) is untouched either way."""

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

    def test_job14_STILL_holds_a_working_turn_even_with_the_proof(self):
        # #333 -- inverted from the pre-reversal "delivers" assertion: a
        # proven origin no longer bypasses the `⏳` marker gate at all.
        tmux, logs = self._job14(self.WORKING, _PROVEN)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("not-a-boundary" in ln for ln in logs), logs)

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

    def test_sync_path_STILL_holds_a_working_turn_even_for_a_proven_boundary(self):
        # #333 -- inverted; uses CB_IDLE_CAP (not CB_BUSY_CAP) so the marker
        # gate is what refuses this, unconfounded with the separate,
        # unconditional busy-skip #333 also added.
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1,
                                         origin=_PROVEN)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(handled)

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
    """#400 update: the Stop hook (notify-compact-request.sh) is now a
    permanent no-op -- it no longer "vetoes" anything because it no longer
    records anything AT ALL, for any message shape, `⏳`-ending or not.
    `test_stop_hook_still_refuses_a_working_turn` is kept as a trivial
    subset of that broader guarantee (this specific historical shape
    still produces no record, same as every other shape now does). The
    class's original point survives in spirit: the SubagentStop channel
    (`notify-compact-subagent-boundary.sh`) is the real source of
    `/compact` requests for an autopilot session now, never the
    supervisor's own turn-ending marker."""

    def test_stop_hook_still_refuses_a_working_turn(self):
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        hook = airuleset.REPO_DIR / "hooks" / "notify-compact-request.sh"
        payload = json.dumps({"session_id": "sv-1", "cwd": "/x",
                              "last_assistant_message":
                                  "## ✅ Work Complete\n⏳ WORKING: ďalší ticket"})
        subprocess.run(["bash", str(hook)], input=payload, text=True,
                       capture_output=True, env={**os.environ, "HOME": home, "AIRULESET_COMPACT_RECORD_HOLD_S": "0"},
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
                           env={**os.environ, "HOME": home, "AIRULESET_COMPACT_RECORD_HOLD_S": "0"},
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

    # -- RECORD, a live-tasks deferral fact carried forward (#246) ---------- #

    def test_a_live_sibling_still_records_naming_the_deferral_fact(self):
        # #246: a sibling worker still running used to be an outright
        # DECLINE here; it now RECORDS the proven boundary like any other
        # and carries the deferral FACT forward as `deferred=live-tasks
        # n=N` on the SAME record line — the live-tasks SAFETY check moved
        # to the two delivery-time gates (`_session_has_live_bg_tasks`).
        r, home = self._run(sid="sup-sib", agent_id="agt-sib", tasks="sibling")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((Path(home) / ".claude" / "compact-requests.json").exists())
        self._expect_one(home, "RECORD", deferred="live-tasks", n="1",
                         agent="agt-sib", sid="sup-sib")

    def test_the_live_task_count_is_the_non_self_count(self):
        _, home = self._run(sid="sup-two", tasks="two-siblings")
        self._expect_one(home, "RECORD", deferred="live-tasks", n="2")

    def test_zero_live_tasks_carries_no_deferred_field_at_all(self):
        # the positive control for the two tests above: a proven boundary
        # with NO sibling work carries no `deferred=` field on its RECORD
        # line — the field is a FACT about deferral, not a constant tag.
        _, home = self._run(sid="sup-zero", tasks="self")
        f = self._expect_one(home, "RECORD", sid="sup-zero")
        self.assertNotIn("deferred", f, f)

    # -- DECLINE, one named predicate each — an UNPROVABLE registry only ---- #

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
        lines = _decision_lines(home)
        self.assertEqual(len(lines), 2, lines)
        outs = [_decision_fields(ln)["_outcome"] for ln in lines]
        self.assertEqual(outs, ["DECLINE", "RECORD"], outs)
        # #246 — the worker's own RECORD line still carries its deferral
        # fact, unaffected by the throttled non-worker DECLINE ahead of it.
        self.assertEqual(_decision_fields(lines[1]).get("deferred"),
                         "live-tasks", lines[1])

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
            capture_output=True, env={**os.environ, "HOME": home, "AIRULESET_COMPACT_RECORD_HOLD_S": "0"},
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
                env={**os.environ, "HOME": home, "AIRULESET_COMPACT_RECORD_HOLD_S": "0"}, cwd=str(airuleset.REPO_DIR),
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

    def test_a_hook_that_logs_unconditionally_fails_the_record_assertions(self):
        # "make something appear in the log" — the exact wrong fix #123 warns
        # about. It writes a RECORD line for every payload, before any
        # predicate is evaluated — so it can never carry the REAL deferral
        # fact (#246) the correct hook's own RECORD line carries.
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
            probe._expect_one(home, "RECORD", deferred="live-tasks", n="1",
                              agent="agt-t1", sid="sup-t1")

    def test_a_hook_that_still_declines_on_live_tasks_fails_the_record_assertions(self):
        # #246's own regression shape: the live-tasks branch reverted to
        # DECLINE-and-exit (the pre-#246 behavior) instead of carrying the
        # deferral fact forward on a RECORD line. Mutating the REAL shipped
        # deferral-carrying statement back to the old decline-and-exit block
        # proves the new RECORD-with-deferred assertions have teeth against
        # exactly the regression this hook's whole fix exists to prevent.
        mut = self._mutant(lambda s: s.replace(
            '[ "$OTHERS" = "0" ] || DEFERRED="deferred=live-tasks n=$OTHERS "\n',
            '[ "$OTHERS" = "0" ] || { _decide_log DECLINE '
            '"reason=live-tasks n=$OTHERS"; exit 0; }\n', 1))
        (_, home), probe = self._probe(mut, sid="sup-t2", agent_id="agt-t2",
                                       tasks="sibling")
        self.assertFalse(
            (Path(home) / ".claude" / "compact-requests.json").exists(),
            "mutant must have reverted to the pre-#246 decline")
        with self.assertRaises(AssertionError):
            probe._expect_one(home, "RECORD", deferred="live-tasks", n="1")

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


class TestCompactRetryUntilHelper(unittest.TestCase):
    """#238-review-style finding 🟡F1 (this ticket's own review, proven) --
    `_compact_retry_until`, the shared bounded-retry helper BOTH
    `deliver_compact_record` and `deliver_compact_self` are built on, had
    ZERO dedicated test coverage: a mutant reverting `deliver_compact_record`
    to a single bare `deliver_compact_now` call (a full revert of the
    🔴1 fix) passed the entire pre-existing suite untouched. These tests
    exercise the helper directly, in isolation, with no real sleeping."""

    def test_returns_the_first_truthy_word_without_ever_sleeping(self):
        calls = []

        def attempt():
            calls.append(1)
            return "sent"
        word = wd._compact_retry_until(
            attempt, hold_s=10, retry_interval=1,
            clock_fn=lambda: 0.0,
            sleep_fn=lambda s: self.fail("must not sleep"))
        self.assertEqual(word, "sent")
        self.assertEqual(len(calls), 1)

    def test_retries_until_a_later_attempt_succeeds(self):
        clock = [0.0]
        results = iter(["", "", "sent"])

        def attempt():
            return next(results)

        def sleep_fn(s):
            clock[0] += s
        word = wd._compact_retry_until(
            attempt, hold_s=10, retry_interval=1,
            clock_fn=lambda: clock[0], sleep_fn=sleep_fn)
        self.assertEqual(word, "sent")

    def test_gives_up_and_returns_blank_once_the_hold_elapses(self):
        clock = [0.0]

        def sleep_fn(s):
            clock[0] += s
        word = wd._compact_retry_until(
            lambda: "", hold_s=3, retry_interval=1,
            clock_fn=lambda: clock[0], sleep_fn=sleep_fn)
        self.assertEqual(word, "")

    def test_an_exception_is_treated_as_a_falsy_attempt_and_retried(self):
        clock = [0.0]
        attempts = [0]

        def attempt():
            attempts[0] += 1
            if attempts[0] < 2:
                raise RuntimeError("boom")
            return "sent"

        def sleep_fn(s):
            clock[0] += s
        word = wd._compact_retry_until(
            attempt, hold_s=10, retry_interval=1,
            clock_fn=lambda: clock[0], sleep_fn=sleep_fn)
        self.assertEqual(word, "sent")
        self.assertEqual(attempts[0], 2)

    def test_each_sleep_is_clamped_to_the_remaining_deadline(self):
        # a mutant dropping the `min(retry_interval, deadline - now)` clamp
        # would sleep the FULL retry_interval on the last iteration too,
        # overshooting the hold instead of landing exactly on it.
        clock = [0.0]
        sleeps = []

        def sleep_fn(s):
            sleeps.append(s)
            clock[0] += s
        wd._compact_retry_until(
            lambda: "", hold_s=7, retry_interval=5,
            clock_fn=lambda: clock[0], sleep_fn=sleep_fn)
        self.assertEqual(sleeps, [5, 2])

    def test_zero_hold_makes_exactly_one_attempt_and_never_sleeps(self):
        calls = []

        def attempt():
            calls.append(1)
            return ""
        word = wd._compact_retry_until(
            attempt, hold_s=0, retry_interval=1,
            clock_fn=lambda: 0.0,
            sleep_fn=lambda s: self.fail("must not sleep on a zero hold"))
        self.assertEqual(word, "")
        self.assertEqual(len(calls), 1)

    def test_negative_hold_behaves_like_zero(self):
        calls = []

        def attempt():
            calls.append(1)
            return ""
        word = wd._compact_retry_until(
            attempt, hold_s=-5, retry_interval=1,
            clock_fn=lambda: 0.0,
            sleep_fn=lambda s: self.fail("must not sleep on a negative hold"))
        self.assertEqual(word, "")
        self.assertEqual(len(calls), 1)

    def test_infinite_hold_is_clamped_to_a_finite_ceiling(self):
        # #238-review-style finding 🔵F8 (this ticket's own review, proven)
        # -- a misconfigured `hold_s=inf` must not turn this into an
        # effectively unbounded loop inside a Stop/SubagentStop hook the
        # harness itself time-limits.
        clock = [0.0]

        def sleep_fn(s):
            clock[0] += s
        word = wd._compact_retry_until(
            lambda: "", hold_s=float("inf"), retry_interval=100,
            clock_fn=lambda: clock[0], sleep_fn=sleep_fn)
        self.assertEqual(word, "")
        self.assertLessEqual(clock[0], wd.COMPACT_RETRY_HOLD_CEILING_S)

    def test_nan_hold_is_treated_as_the_ceiling_not_as_zero(self):
        # nan comparisons are False everywhere -- `max(0.0, nan)` and
        # `nan < x` are both unreliable, so this must be caught explicitly
        # rather than accidentally collapsing to a zero-attempt hold.
        clock = [0.0]

        def sleep_fn(s):
            clock[0] += s
        word = wd._compact_retry_until(
            lambda: "", hold_s=float("nan"), retry_interval=100,
            clock_fn=lambda: clock[0], sleep_fn=sleep_fn)
        self.assertEqual(word, "")
        self.assertGreater(clock[0], 0.0)

    def test_a_negative_retry_interval_never_reaches_sleep_fn_negative(self):
        # a genuine negative interval passed straight to time.sleep()
        # raises ValueError -- must be clamped to a small positive floor
        # instead.
        clock = [0.0]
        sleeps = []

        def sleep_fn(s):
            self.assertGreater(s, 0.0)
            sleeps.append(s)
            clock[0] += s
        word = wd._compact_retry_until(
            lambda: "", hold_s=1, retry_interval=-3,
            clock_fn=lambda: clock[0], sleep_fn=sleep_fn)
        self.assertEqual(word, "")
        self.assertTrue(sleeps)


class TestDeliverCompactRecord(unittest.TestCase):
    """#238-review-style finding 🟡F1 (this ticket's own review) --
    `deliver_compact_record` (the `--record` path's own bounded retry over
    `deliver_compact_now`) had no dedicated test at all; only the CLI
    wiring around it (`TestCompactRequestCli`, `deliver_compact_now`
    entirely mocked) was covered. These drive the REAL `deliver_compact_now`
    through the SAME fake-tmux fixture the sibling `deliver_compact_self`
    tests use, with a synced fake clock, so the age gate genuinely has to
    clear via real (simulated) elapsed time -- no real sleeping."""

    CWD = "/home/newlevel/devel/record-retry"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_a_same_turn_request_defers_then_sends_once_genuinely_aged(self):
        # #238-review 🔴1's own reported production shape: request_ts and
        # the FIRST now_fn() read are always microseconds apart -- the
        # first attempt must defer, and a LATER attempt (after real
        # simulated elapsed time) must clear the gate and actually send.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-record", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        clock = [1_000_000.0]

        def now_fn():
            return clock[0]

        def sleep_fn(s):
            clock[0] += s
        word = wd.deliver_compact_record(
            "sid-record", self.CWD, request_ts=clock[0], run=tmux,
            projects_dir=proj, hold_s=10, retry_interval=1,
            now_fn=now_fn, sleep_fn=sleep_fn, clock_fn=now_fn)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_request_ts_stays_fixed_while_now_advances_across_attempts(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-fixed", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        clock = [2_000_000.0]
        record_ts = clock[0]

        def now_fn():
            return clock[0]

        def sleep_fn(s):
            clock[0] += s
        with m.patch.object(wd, "deliver_compact_now",
                           wraps=wd.deliver_compact_now) as dcn:
            wd.deliver_compact_record(
                "sid-fixed", self.CWD, request_ts=record_ts, run=tmux,
                projects_dir=proj, hold_s=10, retry_interval=1,
                now_fn=now_fn, sleep_fn=sleep_fn, clock_fn=now_fn)
        self.assertGreaterEqual(dcn.call_count, 2)
        for call in dcn.call_args_list:
            self.assertEqual(call.kwargs.get("request_ts"), record_ts)
        nows = [call.kwargs.get("now") for call in dcn.call_args_list]
        self.assertEqual(len(set(nows)), len(nows),
                         "each retry must read a FRESH now: %r" % nows)

    def test_the_real_default_hold_and_interval_clear_the_default_age_gate(self):
        # the shipped defaults (COMPACT_RECORD_HOLD_DEFAULT_S /
        # COMPACT_RECORD_RETRY_INTERVAL_S) must genuinely be enough to
        # clear COMPACT_MIN_REQUEST_AGE_S's own default -- proven against
        # the REAL constants, not a test-chosen hold/interval.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-defaults", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        clock = [3_000_000.0]
        record_ts = clock[0]

        def now_fn():
            return clock[0]

        def sleep_fn(s):
            clock[0] += s
        word = wd.deliver_compact_record(
            "sid-defaults", self.CWD, request_ts=record_ts, run=tmux,
            projects_dir=proj, now_fn=now_fn, sleep_fn=sleep_fn,
            clock_fn=now_fn)
        self.assertEqual(word, "sent")

    def test_a_too_short_explicit_hold_is_floored_past_the_min_age_gate(self):
        # #238-review-style finding 🟡F4 (this ticket's own review, proven)
        # -- an accidentally-too-short POSITIVE hold_s (shorter than the
        # currently-resolved min-age gate) must be widened, never left as
        # a silent off-switch.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-floor", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        clock = [4_000_000.0]
        record_ts = clock[0]

        def now_fn():
            return clock[0]

        def sleep_fn(s):
            clock[0] += s
        # hold_s=1 alone (with the real 0.5s default retry_interval) would
        # give up at t=1.0, still short of the 2.0s default min-age -- the
        # floor must widen it so the gate genuinely gets a chance to clear.
        word = wd.deliver_compact_record(
            "sid-floor", self.CWD, request_ts=record_ts, run=tmux,
            projects_dir=proj, hold_s=1.0, now_fn=now_fn, sleep_fn=sleep_fn,
            clock_fn=now_fn)
        self.assertEqual(word, "sent")

    def test_an_explicit_zero_hold_is_never_floored_exactly_one_attempt(self):
        # hold_s<=0 means "exactly one attempt, no retry at all" -- a
        # DIFFERENT, deliberate meaning tests/callers rely on (e.g. to keep
        # a real end-to-end hook test fast) -- the F4 floor must never
        # touch it.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-zero", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        with m.patch.object(wd, "deliver_compact_now",
                           wraps=wd.deliver_compact_now) as dcn:
            word = wd.deliver_compact_record(
                "sid-zero", self.CWD, request_ts=time.time(), run=tmux,
                projects_dir=proj, hold_s=0,
                sleep_fn=lambda s: self.fail("must not sleep on hold_s=0"))
        self.assertEqual(word, "")
        dcn.assert_called_once()

    def test_hold_exceeds_deliberately_configured_longer_one_is_untouched(self):
        # the floor only ever WIDENS a too-short hold -- a deliberately
        # LONGER hold must be left exactly as configured.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-long", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP, in_mode=True)
        clock = [5_000_000.0]

        def now_fn():
            return clock[0]

        sleeps = []

        def sleep_fn(s):
            sleeps.append(s)
            clock[0] += s
        word = wd.deliver_compact_record(
            "sid-long", self.CWD, request_ts=clock[0], run=tmux,
            projects_dir=proj, hold_s=17, retry_interval=5,
            now_fn=now_fn, sleep_fn=sleep_fn, clock_fn=now_fn)
        self.assertEqual(word, "")
        # the pinned exact sequence for hold_s=17/retry_interval=5 (the
        # SAME shape `deliver_compact_self`'s own hold/give-up test pins)
        # -- proves the configured 17s was honored, not silently floored
        # down to ~2.5s.
        self.assertEqual(sleeps, [5, 5, 5, 2])


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
        # #238-review 🟡6 -- self-callback is no longer exempt from the
        # too-young gate, so the FIRST attempt's own `now_fn()` read must
        # already clear `COMPACT_MIN_REQUEST_AGE_S` past `record_ts` (the
        # PRIOR `now_fn()` read) for delivery to succeed on attempt one,
        # with zero holding -- a synced two-step `now_fn` makes that
        # deterministic without a real sleep.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-imm", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        reqp = self._reqpath()
        sleeps = []
        calls = [0]

        def now_fn():
            calls[0] += 1
            return 2_000_000.0 if calls[0] == 1 else 2_000_010.0

        with m.patch.object(wd, "compact_requests_path", return_value=reqp):
            word, sid = wd.deliver_compact_self(
                run=tmux, projects_dir=proj, pane_env="%5",
                now_fn=now_fn, sleep_fn=lambda s: sleeps.append(s))
        self.assertEqual(word, "sent")
        self.assertEqual(sid, "sid-imm")
        self.assertEqual(sleeps, [])
        self.assertIn("/compact", tmux.typed_texts())

    def test_records_under_the_self_callback_origin(self):
        # #238-review 🟡6 -- a synced two-step `now_fn` keeps this a single
        # fast attempt (the too-young gate no longer exempts self-callback)
        # instead of a real retry sleep on the default clock.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-origin", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        reqp = self._reqpath()
        calls = [0]

        def now_fn():
            calls[0] += 1
            return 5_000_000.0 if calls[0] == 1 else 5_000_010.0

        with m.patch.object(wd, "compact_requests_path", return_value=reqp), \
             m.patch.object(wd, "record_compact_request",
                            wraps=wd.record_compact_request) as rec:
            wd.deliver_compact_self(run=tmux, projects_dir=proj, pane_env="%5",
                                    now_fn=now_fn)
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
        #
        # #238-review 🟡6 -- self-callback is no longer exempt from the
        # too-young gate; a synced two-step `now_fn` keeps this a single
        # fast attempt instead of a real 5s retry sleep on the default
        # clock.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-clear", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        reqp = self._reqpath()
        calls = [0]

        def now_fn():
            calls[0] += 1
            return 4_000_000.0 if calls[0] == 1 else 4_000_010.0

        with m.patch.object(wd, "compact_requests_path", return_value=reqp):
            word, sid = wd.deliver_compact_self(
                run=tmux, projects_dir=proj, pane_env="%5", now_fn=now_fn)
        self.assertEqual(word, "sent")
        d = json.loads(reqp.read_text()) if reqp.exists() else {}
        self.assertNotIn(sid, d)

    def test_delivery_call_itself_carries_the_self_callback_origin(self):
        # #225-review MAJOR finding: only record_compact_request's origin
        # was asserted -- a mutant dropping `origin=` on the delivery call
        # (the thing that actually grants proven-boundary trust) survived.
        #
        # #238-review 🟡6 -- self-callback is no longer exempt from the
        # too-young gate, so a synced two-step `now_fn` (like the sibling
        # "without ever holding" test above) keeps this a single, fast,
        # deterministic attempt instead of needing a real retry sleep.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-deliver-origin", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        reqp = self._reqpath()
        calls = [0]

        def now_fn():
            calls[0] += 1
            return 3_000_000.0 if calls[0] == 1 else 3_000_010.0

        with m.patch.object(wd, "compact_requests_path", return_value=reqp), \
             m.patch.object(wd, "deliver_compact_now",
                            wraps=wd.deliver_compact_now) as dcn:
            wd.deliver_compact_self(run=tmux, projects_dir=proj, pane_env="%5",
                                    now_fn=now_fn)
        dcn.assert_called_once()
        self.assertEqual(dcn.call_args.kwargs.get("origin"), "self-callback")

    def test_request_ts_and_now_are_threaded_into_every_retry(self):
        # #250-review MINOR -- request_ts/now were wired but never locked; a
        # mutant dropping both kwargs would have survived the whole suite.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-ts", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP, in_mode=True)
        reqp = self._reqpath()
        clock = [1_000_000.0]

        def now_fn():
            return clock[0]

        def sleep_fn(s):
            clock[0] += s

        with m.patch.object(wd, "compact_requests_path", return_value=reqp), \
             m.patch.object(wd, "deliver_compact_now",
                            wraps=wd.deliver_compact_now) as dcn:
            wd.deliver_compact_self(
                run=tmux, projects_dir=proj, pane_env="%5",
                hold_s=17, retry_interval=5,
                now_fn=now_fn, sleep_fn=sleep_fn, clock_fn=now_fn)
        self.assertGreaterEqual(dcn.call_count, 2)
        first_ts = dcn.call_args_list[0].kwargs.get("request_ts")
        self.assertEqual(first_ts, 1_000_000.0)
        # request_ts is captured ONCE and never re-derived per retry ...
        for call in dcn.call_args_list:
            self.assertEqual(call.kwargs.get("request_ts"), first_ts)
        # ... while `now` DOES advance across retries, unlike request_ts.
        nows = [call.kwargs.get("now") for call in dcn.call_args_list]
        self.assertEqual(len(set(nows)), len(nows))


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

    def test_job14_STILL_holds_a_working_turn_for_self_callback_origin(self):
        # #333 -- inverted: `⏳` now blocks self-callback exactly like every
        # other origin (see TestWorkingMarkerNoLongerVetoesAProvenBoundary's
        # own class docstring for the full #121→#333 reversal history).
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING)
        path = _write_request(self._p(), self.SID, self.CWD, origin=self.SELF)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("not-a-boundary" in ln for ln in logs), logs)

    def test_job14_STILL_skips_a_busy_pane_for_self_callback_origin(self):
        # #333 -- inverted: a busy pane no longer bypasses the busy-skip for
        # ANY origin, including self-callback (see #122/#333's own reversal
        # comment above TestCompactTicketBoundary's busy-pane tests).
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        path = _write_request(self._p(), self.SID, self.CWD, origin=self.SELF)
        tmux = CompactFakeTmux(CB_BUSY_CAP)
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, {self.SID: (self.PANE, CB_BUSY_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("skip busy" in ln for ln in logs), logs)

    def test_sync_path_STILL_holds_a_working_turn_for_self_callback_origin(self):
        # #333 -- inverted; uses CB_IDLE_CAP so the marker gate (not the
        # separate busy-skip) is unambiguously what refuses this.
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1,
                                         origin=self.SELF)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(handled)

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


class TestDeferredSincePreservedAcrossReRecord(unittest.TestCase):
    """#250-review MAJOR -- `deferred_since` (job 14's grace anchor) must
    survive a re-record for the SAME still-pending session, UNCONDITIONALLY
    -- unlike `origin`, which is only preserved within a short freshness
    window. `ts` still takes the newer call's value either way (only the
    LATEST boundary matters for delivery TARGETING)."""

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def test_deferred_since_survives_a_re_record(self):
        p = self._p()
        wd.record_compact_request("d1", "/x", now=100, path=p)
        d = wd.load_compact_requests(p)
        d["d1"]["deferred_since"] = 50.0   # simulate job 14's own stamp
        p.write_text(json.dumps(d))
        wd.record_compact_request("d1", "/y", now=500, path=p)   # re-record
        d2 = wd.load_compact_requests(p)
        self.assertEqual(d2["d1"]["deferred_since"], 50.0)
        self.assertEqual(d2["d1"]["ts"], 500)      # ts DOES take the new value
        self.assertEqual(d2["d1"]["cwd"], "/y")    # cwd DOES take the new value

    def test_no_prior_deferred_since_means_none_is_invented(self):
        p = self._p()
        wd.record_compact_request("d2", "/x", now=100, path=p)   # no stamp yet
        wd.record_compact_request("d2", "/y", now=200, path=p)
        d = wd.load_compact_requests(p)
        self.assertNotIn("deferred_since", d["d2"])

    def test_a_fresh_session_never_carries_a_stale_deferred_since(self):
        # once an entry is fully DELIVERED it is removed from the file
        # (clear_compact_request / the pop-on-success paths), so a BRAND
        # NEW request for the SAME sid, recorded afresh later, starts with
        # no deferred_since at all -- never resurrecting an old streak.
        p = self._p()
        wd.record_compact_request("d3", "/x", now=100, path=p)
        d = wd.load_compact_requests(p)
        d["d3"]["deferred_since"] = 50.0
        p.write_text(json.dumps(d))
        wd.clear_compact_request("d3", path=p)   # delivered/dropped/expired
        wd.record_compact_request("d3", "/z", now=9000, path=p)   # a later, unrelated boundary
        d2 = wd.load_compact_requests(p)
        self.assertNotIn("deferred_since", d2["d3"])


class TestFirstTsPreservedAcrossReRecord(unittest.TestCase):
    """#400 -- `first_ts` (the ORIGINAL boundary this pending episode was
    first observed at) must survive every re-record for the SAME
    still-pending session, mirroring `deferred_since`/`not_boundary_since`'s
    own unconditional-preservation shape exactly -- `ts` still takes the
    newer call's value (only the LATEST boundary matters for delivery
    TARGETING), but `first_ts` never moves once set."""

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def test_first_ts_is_stamped_on_the_first_record(self):
        p = self._p()
        wd.record_compact_request("f1", "/x", now=100, path=p)
        d = wd.load_compact_requests(p)
        self.assertEqual(d["f1"]["first_ts"], 100)

    def test_first_ts_survives_a_re_record_unconditionally(self):
        p = self._p()
        wd.record_compact_request("f1", "/x", now=100, path=p)
        wd.record_compact_request("f1", "/y", now=500, path=p)   # re-record
        wd.record_compact_request("f1", "/z", now=1200, path=p)  # a second re-record
        d = wd.load_compact_requests(p)
        self.assertEqual(d["f1"]["first_ts"], 100)     # never moves
        self.assertEqual(d["f1"]["ts"], 1200)           # ts DOES take the newest value
        self.assertEqual(d["f1"]["cwd"], "/z")           # cwd DOES take the newest value

    def test_a_fresh_session_never_carries_a_stale_first_ts(self):
        # once an entry is fully delivered/dropped/expired it is removed
        # from the file, so a BRAND NEW request for the SAME sid, recorded
        # afresh later, starts with its OWN fresh first_ts -- never
        # resurrecting an old episode's anchor.
        p = self._p()
        wd.record_compact_request("f2", "/x", now=100, path=p)
        wd.clear_compact_request("f2", path=p)
        wd.record_compact_request("f2", "/z", now=9000, path=p)
        d = wd.load_compact_requests(p)
        self.assertEqual(d["f2"]["first_ts"], 9000)

    def test_a_legacy_entry_with_no_first_ts_gets_one_stamped_fresh(self):
        # migration safety: an entry written by a pre-#400 caller (or a
        # hand-constructed fixture) has no first_ts key at all -- the very
        # NEXT re-record must stamp one (from `now`, never invented from
        # the legacy `ts`), rather than raising or leaving it permanently
        # absent.
        p = self._p()
        wd.record_compact_request("f3", "/x", now=100, path=p)
        d = wd.load_compact_requests(p)
        del d["f3"]["first_ts"]
        p.write_text(json.dumps(d))
        wd.record_compact_request("f3", "/y", now=777, path=p)
        d2 = wd.load_compact_requests(p)
        self.assertEqual(d2["f3"]["first_ts"], 777)


class TestFirstTsCannotResurrectAnExpiredBoundary(unittest.TestCase):
    """#400 -- the whole point of `first_ts`: a session whose EVERY turn
    keeps re-recording the SAME still-pending request (the pre-#400
    bare-`✅ DONE:` trigger this ticket also removes, or any other repeat
    caller) must NOT be able to keep the request perpetually "fresh" by
    refreshing `ts` -- `compact_ticket_boundary`'s own expiry check reads
    `first_ts`, so the TRUE age (since the ORIGINAL boundary) is what
    decides expiry, regardless of how many times the entry was re-recorded
    in between."""

    SID = "sess-firstts-expiry"
    CWD = "/home/x/firstts"
    PANE = "%9"

    def setUp(self):
        _isolate_compact_claims(self)

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def test_repeated_re_record_cannot_keep_an_old_boundary_alive(self):
        path = self._p()
        origin_ts = time.time() - wd.COMPACT_REQUEST_MAX_AGE_S - 60
        now = time.time()
        # the ORIGINAL boundary, long expired --
        wd.record_compact_request(self.SID, self.CWD, now=origin_ts, path=path)
        # -- but re-recorded (ts refreshed) every few "minutes" all the way
        # up to just before `now`, exactly like a repeatedly-firing passive
        # trigger would.
        step = wd.COMPACT_REQUEST_MAX_AGE_S // 4
        t = origin_ts
        while t < now - 1:
            t += step
            wd.record_compact_request(self.SID, self.CWD, now=t, path=path)
        d = wd.load_compact_requests(path)
        self.assertEqual(d[self.SID]["first_ts"], int(origin_ts),
                         "first_ts must still be the ORIGINAL boundary")
        self.assertGreater(d[self.SID]["ts"], origin_ts,
                           "ts is refreshed by every re-record, as designed")
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(
            now, tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)}, path=path)
        self.assertEqual(tmux.typed_texts(), [],
                         "a repeatedly re-recorded but truly-old boundary "
                         "must never be delivered")
        self.assertTrue(any("expired" in ln for ln in logs), logs)
        self.assertNotIn(self.SID, wd.load_compact_requests(path))

    def test_a_genuinely_fresh_repeated_boundary_still_delivers(self):
        # the control -- a session whose repeated re-records are all
        # genuinely RECENT (first_ts itself is fresh) must be completely
        # unaffected by this change.
        path = self._p()
        now = time.time()
        wd.record_compact_request(self.SID, self.CWD, now=now - 120, path=path)
        wd.record_compact_request(self.SID, self.CWD, now=now - 30, path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(
            now, tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)}, path=path)
        self.assertFalse(any("expired" in ln for ln in logs), logs)
        self.assertIn("/compact", tmux.typed_texts())


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


# --------------------------------------------------------------------------- #
# #246 (2026-08-05) — the live-tasks SAFETY check MOVES from RECORD time (the
# SubagentStop hook's old outright DECLINE) to DELIVERY time
# (`_session_has_live_bg_tasks`, checked right before either keystroke-send
# point in `deliver_compact_now` and job 14's `compact_ticket_boundary`).
# --------------------------------------------------------------------------- #

def _sub_agent_transcript(projects_dir, cwd, sid, age_s, now,
                          filename="agent-x.jsonl"):
    """Write <projects_dir>/<encoded-cwd>/<sid>/subagents/<filename> with an
    mtime `age_s` seconds before `now` — the exact shape `subagent_active`
    (job 4's own "is a dispatched worker alive" signal) reads, reused here
    for signal (b) of `_session_has_live_bg_tasks`. ALSO writes the PARENT
    transcript `<projects_dir>/<encoded-cwd>/<sid>.jsonl` (a bare stub) —
    `_transcript_for_session` (the resolver signal (b) uses to find the
    subagents/ dir in the first place) requires that file to exist, exactly
    like a real session's own transcript always does."""
    d = Path(projects_dir) / wd.encode_project_dir(cwd) / sid / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    parent = d.parent.parent / (sid + ".jsonl")
    if not parent.exists():
        parent.write_text('{"type":"assistant"}\n')
    p = d / filename
    p.write_text('{"type":"assistant"}\n')
    os.utime(p, (now - age_s, now - age_s))
    return p


class TestSessionHasLiveBgTasks(unittest.TestCase):
    """`_session_has_live_bg_tasks` — the two independent DELIVERY-time
    signals #246 moved the SubagentStop hook's old RECORD-time live-tasks
    DECLINE into. Either signal true -> True; neither readable -> False
    (deferral is an OPTIMIZATION of an already-real safety property, never
    itself a new way to block on "we don't know")."""

    def _projects(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_true_when_the_pane_capture_shows_the_waiting_row(self):
        pdir = self._projects()   # no subagents dir at all -> signal (b) is False
        cap = "● Hotovo.\n✻ Waiting for 1 background agent to finish\n❯ \n"
        self.assertTrue(wd._session_has_live_bg_tasks(
            "%1", "sid-a", "/proj/a", None, projects_dir=str(pdir),
            captured=cap))

    def test_true_on_a_fresh_subagent_transcript_mtime(self):
        pdir = self._projects()
        now = 1_000_000.0
        _sub_agent_transcript(pdir, "/proj/b", "sid-b", 10, now)
        # `captured` carries NO waiting row -> signal (a) is False; the fresh
        # mtime (signal b) must be sufficient on its own.
        self.assertTrue(wd._session_has_live_bg_tasks(
            "%1", "sid-b", "/proj/b", None, projects_dir=str(pdir),
            now=now, captured="● Hotovo.\n❯ \n"))

    def test_false_on_stale_mtime_and_a_clean_capture(self):
        pdir = self._projects()
        now = 1_000_000.0
        _sub_agent_transcript(pdir, "/proj/c", "sid-c",
                              wd._LIVE_BG_TASK_WINDOW_S + 1, now)
        self.assertFalse(wd._session_has_live_bg_tasks(
            "%1", "sid-c", "/proj/c", None, projects_dir=str(pdir),
            now=now, captured="● Hotovo.\n❯ \n"))

    def test_false_when_nothing_is_readable(self):
        pdir = self._projects()   # empty -- no subagents dir, no pane
        self.assertFalse(wd._session_has_live_bg_tasks(
            "", "sid-d", "/proj/d", None, projects_dir=str(pdir),
            captured=None))

    def test_a_capture_pane_failure_falls_through_to_signal_b(self):
        # exception-safe: a `run` that raises must not propagate out, and
        # must not be mistaken for a positive signal (a) — it falls through
        # to signal (b), which here is genuinely live.
        pdir = self._projects()
        now = 1_000_000.0
        _sub_agent_transcript(pdir, "/proj/e", "sid-e", 5, now)

        def boom(argv, timeout=8):
            raise OSError("tmux gone")

        self.assertTrue(wd._session_has_live_bg_tasks(
            "%1", "sid-e", "/proj/e", boom, projects_dir=str(pdir), now=now))

    def test_a_capture_pane_failure_with_no_subagent_activity_is_false(self):
        pdir = self._projects()

        def boom(argv, timeout=8):
            raise OSError("tmux gone")

        self.assertFalse(wd._session_has_live_bg_tasks(
            "%1", "sid-f", "/proj/f", boom, projects_dir=str(pdir)))

    def test_no_pane_id_skips_signal_a_and_still_checks_signal_b(self):
        pdir = self._projects()
        now = 1_000_000.0
        _sub_agent_transcript(pdir, "/proj/g", "sid-g", 5, now)
        self.assertTrue(wd._session_has_live_bg_tasks(
            "", "sid-g", "/proj/g", None, projects_dir=str(pdir), now=now))


CB_IDLE_WAITING_CAP = ("● Predošlá práca hotová.\n"
                       "✻ Waiting for 1 background agent to finish\n"
                       "❯ \n  ctx ███░  caveman:lite\n")


class TestCompactTicketBoundaryLiveTasksDefer(unittest.TestCase):
    """Job 14 (`compact_ticket_boundary`) — the live-tasks defer check,
    right before EITHER keystroke-sending branch, using the SAME
    `CompactFakeTmux` harness `TestCompactTicketBoundary` uses."""

    PANE = "%9"
    SID = "sess-livebg-14"

    def setUp(self):
        _isolate_compact_claims(self)   # #78 — never touch the real claims file

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, captured):
        proj = self._dir()
        path = str(proj / "compact-requests.json")
        wd.record_compact_request(self.SID, "/home/x/livebg", path=path)
        tmux = CompactFakeTmux(captured)
        panes_by_sid = {self.SID: (self.PANE, captured)}
        logs = wd.compact_ticket_boundary(time.time(), tmux, {}, panes_by_sid,
                                          path=path, projects_dir=proj)
        return tmux, logs, path

    def _go_with_ts(self, captured, record_ts, boundary_now, path=None,
                   cap_seq=()):
        proj = self._dir()
        path = path or str(proj / "compact-requests.json")
        wd.record_compact_request(self.SID, "/home/x/livebg", now=record_ts,
                                  path=path)
        if boundary_now != record_ts:
            # #250-review (MAJOR fix) -- the grace anchor (`deferred_since`)
            # is stamped the FIRST sweep that observes a pending request
            # deferred on live tasks, never derived from `record_ts`
            # directly. Prime it with an earlier sweep AT `record_ts` --
            # no scripted cap_seq needed, since a freshly-stamped anchor is
            # always in-grace and the sweep never proceeds past its own
            # "skip live-tasks" continue -- so the REAL sweep below
            # measures grace from the correct anchor, exactly like the
            # real ~60s-cadence job does across two ticks.
            priming_tmux = CompactFakeTmux(captured)
            wd.compact_ticket_boundary(
                record_ts, priming_tmux, {}, {self.SID: (self.PANE, captured)},
                path=path, projects_dir=proj)
        tmux = CompactFakeTmux(captured, cap_seq=cap_seq)
        panes_by_sid = {self.SID: (self.PANE, captured)}
        logs = wd.compact_ticket_boundary(boundary_now, tmux, {}, panes_by_sid,
                                          path=path, projects_dir=proj)
        return tmux, logs, path

    def test_live_tasks_true_defers_request_kept_no_keystrokes(self):
        # #250 -- record and boundary happen moments apart (both via
        # time.time()), so this pins the IN-GRACE branch specifically: a
        # fresh request under a live-tasks defer is still skipped/kept,
        # exactly like the pre-#250 (unconditional) shape.
        tmux, logs, path = self._go(CB_IDLE_WAITING_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip live-tasks (compact-request)" in ln
                            for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_live_tasks_false_delivers_exactly_as_today(self):
        # positive control -- the SAME fixture, minus the waiting row
        tmux, logs, path = self._go(CB_IDLE_CAP)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertEqual(wd.load_compact_requests(path), {})

    # ------------------------------------------------------------------- #
    # #250 (2026-08-05) — bounding the live-tasks defer by TIME: a session
    # that NEVER goes quiet (a supervisor dispatching workers back to back)
    # must not be deferred forever.
    # ------------------------------------------------------------------- #

    def test_live_tasks_true_within_grace_skips_and_keeps_the_request(self):
        recent_ts = 1_000_000.0
        boundary_now = recent_ts + wd.COMPACT_DEFER_GRACE_S - 1
        tmux, logs, path = self._go_with_ts(CB_IDLE_WAITING_CAP, recent_ts,
                                            boundary_now)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip live-tasks (compact-request)" in ln
                            for ln in logs), logs)
        self.assertFalse(any("grace-elapsed" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_live_tasks_true_past_grace_still_defers_never_sends(self):
        # #400 FIX 5 (inverted from the pre-fix name/assertion, which
        # required this to DELIVER once grace elapsed) — a live gatekeeper
        # incident (2026-08-12) showed exactly this branch firing
        # `/compact` into a session mid-work with 6+ background agents
        # still running. Live background tasks are now an UNCONDITIONAL
        # skip with NO time-based override, at every delivery point: a
        # missed compact is recoverable, a compact fired mid-work is not
        # (it drops a sibling worker's own task linkage — #246's own
        # safety property). `deferred_since`/`COMPACT_DEFER_GRACE_S` are
        # kept ONLY as an observability tag on the skip line.
        old_ts = 1_000_000.0
        boundary_now = old_ts + wd.COMPACT_DEFER_GRACE_S + 1
        tmux, logs, path = self._go_with_ts(CB_IDLE_WAITING_CAP, old_ts,
                                            boundary_now)
        self.assertEqual(tmux.typed_texts(), [], logs)
        self.assertTrue(any(
            "skip live-tasks (compact-request, past-grace)" in ln
            for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_malformed_ts_stays_in_grace_and_is_kept(self):
        # a legacy/corrupted entry with no readable `ts` at all -- unmeasurable
        # age must never be guessed as "past grace" (that would start typing
        # into a session with live siblings the FIRST time age can't be read).
        proj = self._dir()
        path = str(proj / "compact-requests.json")
        Path(path).write_text(json.dumps(
            {self.SID: {"cwd": "/home/x/livebg"}}))   # no "ts" key at all
        tmux = CompactFakeTmux(CB_IDLE_WAITING_CAP)
        panes_by_sid = {self.SID: (self.PANE, CB_IDLE_WAITING_CAP)}
        logs = wd.compact_ticket_boundary(10_000_000.0, tmux, {}, panes_by_sid,
                                          path=path, projects_dir=proj)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip live-tasks (compact-request)" in ln
                            for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    # ------------------------------------------------------------------- #
    # #250-review (MAJOR) — the grace anchor must NOT be `entry["ts"]`,
    # which resets on every re-record: a session completing tickets faster
    # than the grace window (#250's own target population) would otherwise
    # never actually reach it. `deferred_since` is stamped the FIRST sweep
    # a pending request is observed deferred and preserved across re-records.
    # ------------------------------------------------------------------- #

    def test_deferred_since_is_stamped_on_first_defer_and_persisted(self):
        tmux, logs, path = self._go(CB_IDLE_WAITING_CAP)
        self.assertEqual(tmux.sent, [])
        d = wd.load_compact_requests(path)
        self.assertIn("deferred_since", d[self.SID])

    def test_repeated_re_records_do_not_reset_the_grace_anchor(self):
        proj = self._dir()
        path = str(proj / "compact-requests.json")
        t0 = 1_000_000.0
        # First record + first sweep -- stamps deferred_since = t0, in-grace.
        wd.record_compact_request(self.SID, "/home/x/livebg", now=t0, path=path)
        tmux1 = CompactFakeTmux(CB_IDLE_WAITING_CAP)
        wd.compact_ticket_boundary(
            t0, tmux1, {}, {self.SID: (self.PANE, CB_IDLE_WAITING_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux1.sent, [])

        # A SECOND record for the SAME session arrives well inside the
        # grace window (a fast follow-on ticket boundary) -- refreshes
        # `ts`, but must NOT reset `deferred_since`.
        t1 = t0 + wd.COMPACT_DEFER_GRACE_S - 10
        wd.record_compact_request(self.SID, "/home/x/livebg", now=t1, path=path)
        d_mid = wd.load_compact_requests(path)
        self.assertEqual(d_mid[self.SID]["ts"], int(t1))
        self.assertEqual(d_mid[self.SID]["deferred_since"], t0)

        # A sweep run PAST the ORIGINAL deferred_since + grace (but well
        # within grace of the SECOND record's own `ts`) must still SKIP --
        # #400 FIX 5 removed the "deliver once grace elapses" escape
        # entirely (see the sibling grace test above), so this now proves
        # the anchor-preservation property a different way: the skip
        # line's own "past-grace" tag can ONLY be correct if `deferred_since`
        # really did stay `t0` (not reset to `t1`/`t2` by the re-record) --
        # an anchor keyed on `ts` would still read "within grace" here
        # (`t2 - t1 == 15`, well under `COMPACT_DEFER_GRACE_S`) and the
        # log line would say so instead.
        t2 = t0 + wd.COMPACT_DEFER_GRACE_S + 5
        tmux2 = CompactFakeTmux(CB_IDLE_WAITING_CAP)
        logs2 = wd.compact_ticket_boundary(
            t2, tmux2, {}, {self.SID: (self.PANE, CB_IDLE_WAITING_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux2.typed_texts(), [], logs2)
        self.assertTrue(any(
            "skip live-tasks (compact-request, past-grace)" in ln
            for ln in logs2), logs2)
        self.assertEqual(
            wd.load_compact_requests(path)[self.SID]["deferred_since"], t0)

    def test_past_grace_draft_branch_also_stays_deferred(self):
        # #400 FIX 5 (inverted, same reasoning as the idle-branch sibling
        # test above) -- the DRAFT/stash branch gets the SAME unconditional
        # live-tasks skip, checked ONCE before the draft branch is ever
        # reached: a genuinely-live sibling worker must defer this delivery
        # regardless of how long it has been deferred, and regardless of
        # whether the pane happens to be holding an unsent draft. No
        # cap_seq is scripted here on purpose (an empty cap_seq just
        # returns the SAME static `draft_cap` for every capture-pane
        # call, per `CompactFakeTmux`'s own docstring) -- if the stash
        # dance (`_compact_stash_attempt`) were ever reached despite the
        # live-tasks skip, it would misread the unchanging static
        # capture as its own bare/typed/submitted sequence; the empty
        # `tmux.typed_texts()` assertion below is the direct proof it
        # was never entered.
        old_ts = 1_000_000.0
        boundary_now = old_ts + wd.COMPACT_DEFER_GRACE_S + 1
        draft_cap = ("● Predošlá práca hotová.\n"
                    "✻ Waiting for 1 background agent to finish\n"
                    "❯ nedokončená veta\n")
        tmux, logs, path = self._go_with_ts(draft_cap, old_ts, boundary_now)
        self.assertEqual(tmux.typed_texts(), [], logs)
        self.assertTrue(any(
            "skip live-tasks (compact-request, past-grace)" in ln
            for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))


class TestCompactDeferGraceRelationship(unittest.TestCase):
    """#250 -- the grace window MUST stay well under the request TTL: a
    grace >= the TTL recreates the exact starvation this fix exists to
    kill (a request would LAPSE, dropped unfired, before its own grace
    window ever has a chance to elapse)."""

    def test_grace_stays_comfortably_under_the_request_ttl(self):
        self.assertLess(wd.COMPACT_DEFER_GRACE_S, wd.COMPACT_REQUEST_MAX_AGE_S)


class TestCompactLiveTasksInGrace(unittest.TestCase):
    """#250 -- `_compact_live_tasks_in_grace`, the shared time-bound check
    both delivery points consult before honoring a live-tasks defer."""

    def test_within_grace_is_true(self):
        self.assertTrue(wd._compact_live_tasks_in_grace(
            1000, 1000 + wd.COMPACT_DEFER_GRACE_S - 1))

    def test_exactly_at_grace_is_no_longer_in_grace(self):
        # strict "<" -- the boundary instant itself already counts as past
        self.assertFalse(wd._compact_live_tasks_in_grace(
            1000, 1000 + wd.COMPACT_DEFER_GRACE_S))

    def test_past_grace_is_false(self):
        self.assertFalse(wd._compact_live_tasks_in_grace(
            1000, 1000 + wd.COMPACT_DEFER_GRACE_S + 1))

    def test_missing_ts_stays_in_grace(self):
        self.assertTrue(wd._compact_live_tasks_in_grace(None, 10_000_000))

    def test_non_numeric_ts_stays_in_grace(self):
        self.assertTrue(
            wd._compact_live_tasks_in_grace("not-a-number", 10_000_000))

    def test_env_override_is_honored(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_DEFER_GRACE_S": "10"}):
            self.assertFalse(wd._compact_live_tasks_in_grace(1000, 1015))
            self.assertTrue(wd._compact_live_tasks_in_grace(1000, 1005))

    def test_explicit_grace_param_overrides_env_and_default(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_DEFER_GRACE_S": "9999"}):
            self.assertFalse(
                wd._compact_live_tasks_in_grace(1000, 1050, grace=10))

    # ------------------------------------------------------------------- #
    # #250-review (MINOR) — a misconfigured env override must never disable
    # the live-tasks safety defer outright (0/negative) or recreate the
    # lapse-before-grace starvation (>= the request TTL).
    # ------------------------------------------------------------------- #

    def test_env_override_negative_is_clamped_to_a_minimum(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_DEFER_GRACE_S": "-100"}):
            self.assertEqual(wd._compact_defer_grace(), 1)

    def test_env_override_zero_is_clamped_to_a_minimum(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_DEFER_GRACE_S": "0"}):
            self.assertEqual(wd._compact_defer_grace(), 1)

    def test_env_override_at_or_above_ttl_is_clamped_below_it(self):
        huge = str(wd.COMPACT_REQUEST_MAX_AGE_S + 1000)
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_DEFER_GRACE_S": huge}):
            self.assertLess(wd._compact_defer_grace(), wd.COMPACT_REQUEST_MAX_AGE_S)

    def test_env_override_exactly_at_ttl_is_clamped_below_it(self):
        at_ttl = str(wd.COMPACT_REQUEST_MAX_AGE_S)
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_DEFER_GRACE_S": at_ttl}):
            self.assertLess(wd._compact_defer_grace(), wd.COMPACT_REQUEST_MAX_AGE_S)

    def test_a_sane_env_override_is_returned_unclamped(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_DEFER_GRACE_S": "600"}):
            self.assertEqual(wd._compact_defer_grace(), 600)

    def test_explicit_grace_param_is_never_clamped(self):
        # every production call leaves grace=None; an explicit override
        # (test/caller-only) is returned verbatim, even outside the sane
        # range -- the clamp protects only the env/const-derived default.
        self.assertEqual(wd._compact_defer_grace(grace=-5), -5)
        self.assertEqual(
            wd._compact_defer_grace(grace=wd.COMPACT_REQUEST_MAX_AGE_S + 5),
            wd.COMPACT_REQUEST_MAX_AGE_S + 5)


class TestDeliverCompactNowLiveTasksDefer(unittest.TestCase):
    """`deliver_compact_now` (the synchronous #65 path) — the same
    live-tasks defer check, right before its own single keystroke-sending
    point, using the SAME `DeliverCompactNowFakeTmux` harness
    `TestDeliverCompactNow` uses."""

    SID = "sess-livebg-dcn"
    CWD = "/home/newlevel/devel/livebg-dcn"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, captured):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                    projects_dir=proj)
        return ok, tmux

    def _go_with_ts(self, captured, request_ts=None, now=None):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                    projects_dir=proj,
                                    request_ts=request_ts, now=now)
        return ok, tmux

    def test_live_tasks_true_falls_back_to_recorded(self):
        # #250 -- no request_ts passed (every pre-#250 caller/test): treated
        # as unmeasurable -> ALWAYS in-grace -> UNCHANGED behavior.
        ok, tmux = self._go(CB_IDLE_WAITING_CAP)
        self.assertFalse(ok)          # "" -- falls through to job 14's retry
        self.assertEqual(tmux.sent, [])

    def test_live_tasks_false_still_delivers(self):
        # positive control -- the SAME fixture, minus the waiting row
        ok, tmux = self._go(CB_IDLE_CAP)
        self.assertTrue(ok)
        self.assertIn("/compact", tmux.typed_texts())

    # ------------------------------------------------------------------- #
    # #250 (2026-08-05) — the same time-bound check, exercised directly via
    # the new `request_ts=`/`now=` params.
    # ------------------------------------------------------------------- #

    def test_live_tasks_true_within_grace_still_falls_back(self):
        recent_ts = 1_000_000.0
        now = recent_ts + wd.COMPACT_DEFER_GRACE_S - 1
        ok, tmux = self._go_with_ts(CB_IDLE_WAITING_CAP, request_ts=recent_ts,
                                    now=now)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SKIP live-tasks", log_text)
        self.assertNotIn("grace-elapsed", log_text)

    def test_live_tasks_true_past_grace_still_falls_back(self):
        # #400 FIX 5 (inverted, same reasoning as job 14's own sibling
        # test) -- this synchronous path also LOSES its "deliver anyway
        # once past COMPACT_DEFER_GRACE_S" escape. Live background tasks
        # are now an unconditional SKIP here too, with no time-based
        # override; the request falls back to job 14's polled retry
        # exactly like every other "not safe right now" state this
        # function refuses on.
        old_ts = 1_000_000.0
        now = old_ts + wd.COMPACT_DEFER_GRACE_S + 1
        ok, tmux = self._go_with_ts(CB_IDLE_WAITING_CAP, request_ts=old_ts,
                                    now=now)
        self.assertFalse(ok)
        self.assertEqual(tmux.typed_texts(), [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SKIP live-tasks sid=%s cwd=%s" % (self.SID, self.CWD),
                      log_text)

    def test_no_request_ts_defaults_to_in_grace_even_with_a_far_future_now(self):
        # #250 -- documents the deliberate default: this function runs
        # SYNCHRONOUSLY, moments after the request was recorded with
        # ts=now, so its own attempt is ALWAYS in-grace when tasks are
        # live -- a huge `now` with no `request_ts` must still defer.
        ok, tmux = self._go_with_ts(CB_IDLE_WAITING_CAP, request_ts=None,
                                    now=99_999_999.0)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])


# --------------------------------------------------------------------------- #
# #238 (2026-08-06) — the same-turn dispatch race: `_session_has_live_bg_tasks`
# returning False is not trustworthy for a request younger than
# COMPACT_MIN_REQUEST_AGE_S (see the section comment in watchdog/__init__.py
# right above `_compact_request_too_young`).
# --------------------------------------------------------------------------- #

class TestDeliverCompactNowMinRequestAge(unittest.TestCase):
    SID = "sess-minage-1"
    CWD = "/home/newlevel/devel/minage"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, request_ts, now, live=False):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "_session_has_live_bg_tasks",
                           return_value=live):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                        projects_dir=proj,
                                        origin="subagent-stop",
                                        request_ts=request_ts, now=now)
        return ok, tmux

    def test_a_fresh_request_with_no_live_signal_still_defers(self):
        # the same-turn dispatch race: request_ts and now only milliseconds
        # apart, exactly like the real synchronous path -- a "no live
        # tasks" verdict this fresh must not be trusted yet.
        now = 1_000_000.0
        ok, tmux = self._go(request_ts=now, now=now, live=False)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SKIP too-young", log_text)

    def test_the_same_request_delivers_once_aged_past_the_gate(self):
        request_ts = 1_000_000.0
        later = request_ts + wd.COMPACT_MIN_REQUEST_AGE_S + 1
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        alive_proc = _alive_proc_fingerprint(self)
        with m.patch.object(wd, "_session_has_live_bg_tasks",
                           return_value=False), \
             m.patch.object(wd, "_pane_claude_proc_fingerprint",
                           return_value=alive_proc):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                        projects_dir=proj,
                                        origin="subagent-stop",
                                        request_ts=request_ts, now=later)
        self.assertEqual(ok, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_a_genuinely_live_signal_is_unaffected_by_the_age_gate(self):
        # the age gate only matters on the "no live tasks" branch -- a
        # REAL live-tasks defer (existing #246/#250 behavior) is untouched,
        # fresh request_ts or not.
        now = 1_000_000.0
        ok, tmux = self._go(request_ts=now, now=now, live=True)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SKIP live-tasks", log_text)
        self.assertNotIn("too-young", log_text)

    def test_no_request_ts_at_all_is_a_complete_noop(self):
        # every pre-#238 caller (request_ts=None default) sees NO behavior
        # change at all.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        alive_proc = _alive_proc_fingerprint(self)
        with m.patch.object(wd, "_session_has_live_bg_tasks",
                           return_value=False), \
             m.patch.object(wd, "_pane_claude_proc_fingerprint",
                           return_value=alive_proc):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                        projects_dir=proj)
        self.assertEqual(ok, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_self_callback_origin_is_also_subject_to_the_age_gate(self):
        # #238-review 🟡6 -- the self-callback exemption was REMOVED: its
        # own caller (`deliver_compact_self`) already retries with a
        # FRESH `now_fn()` on every attempt, so its own second attempt (a
        # few real seconds later) clears this gate on genuinely-elapsed
        # time -- no per-origin exemption needed any more, and the prose
        # constraint the old exemption rested on ("self-callback's own
        # protocol never dispatches in the same turn") is nothing more
        # than that: prose, defeated by a parallel tool-call batch. A
        # same-turn-fresh request_ts under this origin now defers exactly
        # like every other origin (see the sibling `subagent-stop` test
        # `test_a_fresh_request_with_no_live_signal_still_defers` above).
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        now = 1_000_000.0
        with m.patch.object(wd, "_session_has_live_bg_tasks",
                           return_value=False):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                        projects_dir=proj,
                                        origin="self-callback",
                                        request_ts=now, now=now)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SKIP too-young", log_text)


class TestCompactRequestTooYoungHelper(unittest.TestCase):
    """Unit coverage of `_compact_request_too_young` in isolation."""

    def test_none_request_ts_is_never_too_young(self):
        self.assertFalse(wd._compact_request_too_young(None, 1000.0))

    def test_fresh_request_is_too_young(self):
        self.assertTrue(wd._compact_request_too_young(1000.0, 1000.5))

    def test_aged_request_is_not_too_young(self):
        self.assertFalse(wd._compact_request_too_young(
            1000.0, 1000.0 + wd.COMPACT_MIN_REQUEST_AGE_S + 1))

    def test_unmeasurable_now_never_raises_and_is_not_too_young(self):
        self.assertFalse(wd._compact_request_too_young(1000.0, "not-a-number"))

    def test_env_override_widens_the_window(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_COMPACT_MIN_REQUEST_AGE_S": "10"}):
            self.assertTrue(wd._compact_request_too_young(1000.0, 1005.0))

    def test_nonpositive_env_override_falls_back_to_the_default(self):
        # a misconfigured 0/negative override must not silently disable
        # the gate outright (every request would read as already old
        # enough) -- same clamp shape `_compact_defer_grace` already uses.
        with m.patch.dict(os.environ,
                          {"AIRULESET_COMPACT_MIN_REQUEST_AGE_S": "0"}):
            self.assertTrue(wd._compact_request_too_young(1000.0, 1000.5))


# --------------------------------------------------------------------------- #
# #238 — the thin-context gate: zero real `assistant` activity since the
# session's last `compact_boundary` entry means "Not enough messages to
# compact" is coming, so drop the request instead of sending and stranding a
# claim.
# --------------------------------------------------------------------------- #

def _write_boundary_transcript(base, cwd, sid, n_assistant_after,
                               ctx_tokens=300_000, boundary_ts=None):
    """A transcript with ONE `compact_boundary` entry followed by
    `n_assistant_after` real `assistant` entries. The LAST such entry (if
    any) carries usage data so a caller that ALSO reads
    `transcript_current_context` (the #48 gate, exercised alongside #238 in
    `deliver_compact_now`/job 14) still sees a real context size rather than
    0."""
    from datetime import datetime, timezone
    d = Path(base) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    ts = boundary_ts if boundary_ts is not None else 1_700_000_000.0
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    lines = [json.dumps({"type": "system", "subtype": "compact_boundary",
                         "timestamp": iso,
                         "compactMetadata": {"preTokens": 1, "postTokens": 1}})]
    for i in range(n_assistant_after):
        usage = ({"cache_read_input_tokens": ctx_tokens,
                  "cache_creation_input_tokens": 0}
                 if i == n_assistant_after - 1 else {})
        lines.append(json.dumps({"type": "assistant", "message": {
            "id": "msg_%d" % i, "content": "turn %d" % i, "usage": usage}}))
    p.write_text("\n".join(lines) + "\n")
    return p


class TestCompactMessagesSinceBoundary(unittest.TestCase):
    """Unit coverage of `_compact_messages_since_boundary` /
    `_compact_thin_context` in isolation."""

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_zero_assistant_entries_since_the_boundary(self):
        proj = self._dir()
        p = _write_boundary_transcript(proj, "/x", "sid-z", 0)
        delta, boundary_ts = wd._compact_messages_since_boundary(str(p))
        self.assertEqual(delta, 0)
        self.assertIsNotNone(boundary_ts)

    def test_several_assistant_entries_since_the_boundary(self):
        proj = self._dir()
        p = _write_boundary_transcript(proj, "/x", "sid-many", 28)
        delta, boundary_ts = wd._compact_messages_since_boundary(str(p))
        self.assertEqual(delta, 28)
        self.assertIsNotNone(boundary_ts)

    def test_non_assistant_entries_after_the_boundary_do_not_count(self):
        # #238-review 🟡4 -- a mutant dropping the `type == "assistant"`
        # filter (counting EVERY entry after the boundary) must be caught:
        # a boundary followed only by `user`/`system`/other non-assistant
        # entries is still THIN (delta stays 0), even though several
        # entries genuinely exist after it.
        proj = self._dir()
        d = Path(proj) / wd.encode_project_dir("/x")
        d.mkdir(parents=True, exist_ok=True)
        p = d / "sid-mixed.jsonl"
        lines = [
            json.dumps({"type": "system", "subtype": "compact_boundary",
                       "timestamp": "2026-08-01T00:00:00.000Z"}),
            json.dumps({"type": "user", "message": {"content": "hi"}}),
            json.dumps({"type": "system", "subtype": "other"}),
            json.dumps({"type": "queue-operation", "content": "/compact"}),
        ]
        p.write_text("\n".join(lines) + "\n")
        delta, boundary_ts = wd._compact_messages_since_boundary(str(p))
        self.assertEqual(delta, 0)
        self.assertIsNotNone(boundary_ts)

    def test_only_the_newest_of_two_boundaries_is_used(self):
        # #238-review 🟡5 -- a mutant reading the FIRST `compact_boundary`
        # match instead of the last must be caught: activity that happened
        # strictly BETWEEN an older and a newer boundary must never count
        # toward the newer boundary's own delta.
        proj = self._dir()
        d = Path(proj) / wd.encode_project_dir("/x")
        d.mkdir(parents=True, exist_ok=True)
        p = d / "sid-two-boundaries.jsonl"
        lines = [
            json.dumps({"type": "system", "subtype": "compact_boundary",
                       "timestamp": "2026-08-01T00:00:00.000Z"}),
            json.dumps({"type": "assistant", "message": {"id": "a1"}}),
            json.dumps({"type": "assistant", "message": {"id": "a2"}}),
            json.dumps({"type": "system", "subtype": "compact_boundary",
                       "timestamp": "2026-08-01T00:10:00.000Z"}),
        ]
        p.write_text("\n".join(lines) + "\n")
        delta, boundary_ts = wd._compact_messages_since_boundary(str(p))
        self.assertEqual(delta, 0)
        # the NEWER boundary's own timestamp, not the older one.
        from datetime import datetime, timezone
        self.assertEqual(
            boundary_ts,
            datetime(2026, 8, 1, 0, 10, 0, tzinfo=timezone.utc).timestamp())

    def test_no_boundary_at_all_is_unmeasurable(self):
        proj = self._dir()
        p = _write_ctx_transcript(proj, "/x", "sid-none", 300_000)
        delta, boundary_ts = wd._compact_messages_since_boundary(str(p))
        self.assertIsNone(boundary_ts)

    def test_a_boundary_with_an_unparseable_timestamp_still_measures_delta(self):
        # #238-review-style finding 🔵F5 (this ticket's own review, proven)
        # -- a boundary that WAS found but whose `timestamp` field is
        # malformed/missing must NOT be conflated with "no boundary found
        # at all": `delta` is still genuinely measurable and must not be
        # silently discarded as unmeasurable.
        proj = self._dir()
        d = Path(proj) / wd.encode_project_dir("/x")
        d.mkdir(parents=True, exist_ok=True)
        p = d / "sid-badts.jsonl"
        lines = [
            json.dumps({"type": "system", "subtype": "compact_boundary",
                       "timestamp": "not-a-real-timestamp"}),
            json.dumps({"type": "assistant", "message": {"id": "a1"}}),
        ]
        p.write_text("\n".join(lines) + "\n")
        delta, boundary_ts = wd._compact_messages_since_boundary(str(p))
        self.assertEqual(delta, 1)
        self.assertIsNotNone(boundary_ts)

    def test_a_boundary_with_a_missing_timestamp_field_still_measures_delta(self):
        proj = self._dir()
        d = Path(proj) / wd.encode_project_dir("/x")
        d.mkdir(parents=True, exist_ok=True)
        p = d / "sid-notimestamp.jsonl"
        lines = [
            json.dumps({"type": "system", "subtype": "compact_boundary"}),
        ]
        p.write_text("\n".join(lines) + "\n")
        delta, boundary_ts = wd._compact_messages_since_boundary(str(p))
        self.assertEqual(delta, 0)
        self.assertIsNotNone(boundary_ts)

    def test_missing_file_is_unmeasurable_not_thin(self):
        delta, boundary_ts = wd._compact_messages_since_boundary(
            "/no/such/file.jsonl")
        self.assertEqual(delta, 0)
        self.assertIsNone(boundary_ts)

    def test_thin_context_true_on_zero_activity(self):
        proj = self._dir()
        _write_boundary_transcript(proj, "/x", "sid-thin", 0)
        self.assertTrue(wd._compact_thin_context("/x", "sid-thin",
                                                 projects_dir=proj))

    def test_thin_context_false_exactly_at_the_threshold(self):
        # #238-review-style finding 🔵F7 (this ticket's own review) -- a
        # mutant widening `delta < threshold` to `delta <= threshold`
        # survived the whole pre-existing suite, since no fixture ever
        # used `delta == COMPACT_THIN_CONTEXT_MIN_MESSAGES` exactly (only
        # 0 and 26/28). The threshold is exclusive: exactly enough real
        # activity must NOT read as thin.
        proj = self._dir()
        _write_boundary_transcript(proj, "/x", "sid-exact",
                                   wd.COMPACT_THIN_CONTEXT_MIN_MESSAGES)
        self.assertFalse(wd._compact_thin_context("/x", "sid-exact",
                                                   projects_dir=proj))

    def test_thin_context_false_on_real_activity(self):
        proj = self._dir()
        _write_boundary_transcript(proj, "/x", "sid-real", 26)
        self.assertFalse(wd._compact_thin_context("/x", "sid-real",
                                                   projects_dir=proj))

    def test_thin_context_false_with_no_prior_boundary_at_all(self):
        proj = self._dir()
        _write_ctx_transcript(proj, "/x", "sid-first", 300_000)
        self.assertFalse(wd._compact_thin_context("/x", "sid-first",
                                                   projects_dir=proj))

    def test_thin_context_true_on_zero_activity_with_an_unparseable_ts(self):
        # #238-review-style finding 🔵F5 (this ticket's own review, proven)
        # -- a boundary WAS found but its timestamp is malformed must
        # still correctly read as THIN when the real delta is zero --
        # never silently fail open just because the timestamp parse
        # failed.
        proj = self._dir()
        d = Path(proj) / wd.encode_project_dir("/x")
        d.mkdir(parents=True, exist_ok=True)
        p = d / "sid-thin-badts.jsonl"
        p.write_text(json.dumps({"type": "system",
                                 "subtype": "compact_boundary",
                                 "timestamp": "garbage"}) + "\n")
        self.assertTrue(wd._compact_thin_context("/x", "sid-thin-badts",
                                                  projects_dir=proj))

    def test_thin_context_false_when_no_transcript_resolves(self):
        proj = self._dir()
        self.assertFalse(wd._compact_thin_context("/x", "sid-missing",
                                                   projects_dir=proj))


class TestDeliverCompactNowThinContext(unittest.TestCase):
    SID = "sess-thin-dcn"
    CWD = "/home/newlevel/devel/thin-dcn"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_zero_activity_since_boundary_drops_with_no_keystrokes(self):
        # #238-review 🟡3 -- the synchronous path now DEFERS ("", falsy,
        # non-consuming — the caller keeps the request recorded for job
        # 14's later re-check) rather than dropping outright, since a
        # zero-activity read here can be a false zero from a not-yet-
        # flushed transcript. See `deliver_compact_now`'s own docstring.
        proj = self._dir()
        _write_boundary_transcript(proj, self.CWD, self.SID, 0)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                    projects_dir=proj,
                                    origin="subagent-stop")
        self.assertEqual(ok, "")
        self.assertEqual(tmux.sent, [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SKIP thin-context", log_text)

    def test_real_activity_since_boundary_still_delivers(self):
        proj = self._dir()
        _write_boundary_transcript(proj, self.CWD, self.SID, 26)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                    projects_dir=proj,
                                    origin="subagent-stop")
        self.assertEqual(ok, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_zero_activity_is_dropped_even_for_a_proven_boundary_origin(self):
        # #238 -- deliberately NOT exempted for subagent-stop/self-callback,
        # unlike #99/#48 (#126). The live incident this exists for WAS
        # origin=subagent-stop. #238-review 🟡3 -- "dropped" here means
        # DEFERRED ("", falsy — see the sibling test above), not consumed.
        for origin in ("subagent-stop", "self-callback"):
            with self.subTest(origin=origin):
                proj = self._dir()
                sid = self.SID + "-" + origin
                _write_boundary_transcript(proj, self.CWD, sid, 0)
                tmux = DeliverCompactNowFakeTmux(
                    [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
                ok = wd.deliver_compact_now(sid, self.CWD, run=tmux,
                                            projects_dir=proj, origin=origin)
                self.assertEqual(ok, "")
                self.assertEqual(tmux.sent, [])

    def test_first_ever_boundary_is_never_treated_as_thin(self):
        # no PRIOR compact_boundary exists at all -- unmeasurable, must
        # never block the first real compaction of a session's life.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                    projects_dir=proj)
        self.assertEqual(ok, "sent")
        self.assertIn("/compact", tmux.typed_texts())


class TestCompactTicketBoundaryThinContext(unittest.TestCase):
    PANE = "%9"
    SID = "sess-thin-j14"
    CWD = "/home/x/thin-j14"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, n_assistant_after, origin=None):
        proj = self._dir()
        _write_boundary_transcript(proj, self.CWD, self.SID, n_assistant_after)
        path = str(proj / "compact-requests.json")
        wd.record_compact_request(self.SID, self.CWD, path=path, origin=origin)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        panes_by_sid = {self.SID: (self.PANE, CB_IDLE_CAP)}
        logs = wd.compact_ticket_boundary(time.time(), tmux, {}, panes_by_sid,
                                          path=path, projects_dir=proj)
        return tmux, logs, path

    def test_zero_activity_drops_the_request_with_no_keystrokes(self):
        tmux, logs, path = self._go(0, origin="subagent-stop")
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip thin-context (compact-request)" in ln
                            for ln in logs), logs)
        self.assertEqual(wd.load_compact_requests(path), {})

    def test_real_activity_still_delivers(self):
        tmux, logs, path = self._go(26, origin="subagent-stop")
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)


# --------------------------------------------------------------------------- #
# #238 defect 3 — a thin-context DROP must never set the shared claim, so a
# later legitimate send for the SAME session is never blocked by a stranded
# claim (the "Not enough messages to compact" shape #140's TTL used to be
# the only thing that ever released).
# --------------------------------------------------------------------------- #

class TestThinContextNeverStrandsAClaim(unittest.TestCase):
    """#238-review 🔴2 -- the original version of this class was VACUOUS:
    `DeliverCompactNowFakeTmux`'s `display-message` fake returns a bogus
    non-numeric pane pid, so `_pane_claude_proc_fingerprint` never resolves
    through it and any claim a mutant wrongly set would ALREADY read back
    as proc-less/inactive (#83's own "a proc-less entry is dropped"
    semantics) -- `compact_claim_active` would report False whether or not
    the fix under test actually works. Every test here now patches
    `_pane_claude_proc_fingerprint` to a genuine alive fingerprint
    (`_alive_proc_fingerprint`) so a wrongly-set claim WOULD persist and
    be caught, exactly like the sibling proc-fingerprint tests elsewhere
    in this file (#83)."""
    SID = "sess-thin-claim"
    CWD = "/home/newlevel/devel/thin-claim"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_a_thin_defer_never_sets_the_shared_claim(self):
        proj = self._dir()
        _write_boundary_transcript(proj, self.CWD, self.SID, 0)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        alive_proc = _alive_proc_fingerprint(self)
        with m.patch.object(wd, "_pane_claude_proc_fingerprint",
                           return_value=alive_proc):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                        projects_dir=proj, origin="subagent-stop")
        # #238-review 🟡3 -- a thin read on the SYNCHRONOUS path now
        # DEFERS ("", falsy, non-consuming) rather than dropping outright.
        self.assertEqual(ok, "")
        self.assertFalse(wd.compact_claim_active(self.SID, self.CWD,
                                                 projects_dir=proj))

    def test_a_later_genuine_send_for_the_same_session_is_not_blocked(self):
        # simulates: a thin request defers (no claim set), then MORE real
        # conversation happens and a genuine follow-up request for the SAME
        # session must be able to send immediately -- nothing stranded.
        proj = self._dir()
        _write_boundary_transcript(proj, self.CWD, self.SID, 0)
        tmux1 = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        alive_proc = _alive_proc_fingerprint(self)
        with m.patch.object(wd, "_pane_claude_proc_fingerprint",
                           return_value=alive_proc):
            ok1 = wd.deliver_compact_now(self.SID, self.CWD, run=tmux1,
                                         projects_dir=proj, origin="subagent-stop")
            self.assertEqual(ok1, "")
            # now the session gains real activity since the boundary
            _write_boundary_transcript(proj, self.CWD, self.SID, 3)
            tmux2 = DeliverCompactNowFakeTmux(
                [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
            ok2 = wd.deliver_compact_now(self.SID, self.CWD, run=tmux2,
                                         projects_dir=proj, origin="subagent-stop")
        self.assertEqual(ok2, "sent")
        self.assertIn("/compact", tmux2.typed_texts())


# --------------------------------------------------------------------------- #
# #333 (2026-08-08, three same-day live incidents on dev1's own supervisor
# session, forensically traced against its real transcript JSONL +
# compact-sync.log + journalctl — see the issue's design comment for the
# full trace) — `/compact` landed right after a `❓ ASKED`/`⏳ WORKING` turn,
# TWICE, then a THIRD time after a plain `⏳ WORKING` turn with no question
# at all (5 background workers in flight, no Work Complete).
#
# Root cause, confirmed from the transcript's own `queue-operation`
# entries: `/compact` was TYPED into a BUSY pane (permitted for a
# `proven_boundary` origin — #122/#301 — and unconditionally in
# `deliver_compact_now` — #65) at a moment when the marker read was safe,
# then sat QUEUED through several `/goal`-loop-rejected continuations, and
# only DRAINED (executed) at whichever turn's Stop was next genuinely
# ACCEPTED — which, under an actively-working `/goal` loop, is essentially
# always either true completion or a `❓`/`⏳`-blocked turn. The
# marker-freshness check at TYPE time cannot see what the CURRENTLY-BUSY
# generation will eventually produce, so it cannot prevent this. The THIRD
# occurrence additionally exposed `_COMPACT_NON_BOUNDARY_MARKERS_PROVEN`
# (only `❓`, not `⏳`, blocked a proven-boundary origin — #121) combined
# with `COMPACT_DEFER_GRACE_S`'s "deliver anyway" grace-elapsed override:
# together they let a plain, question-free `⏳ WORKING` progress turn get
# compacted the instant the live-tasks grace window ran out.
#
# THE FIX (user directive, overrides #121's own reasoning — #121 assumed a
# supervisor session "never ends any other way than ⏳"; live evidence
# refutes it: this exact session's two confirmed-clean compacts both
# landed on a literal `✅ DONE` terminal turn):
#
#   1. `_compact_not_at_boundary` no longer special-cases a proven-boundary
#      origin's marker set — `❓` AND `⏳` block delivery for EVERY origin.
#   2. `kind == "busy"` ALWAYS blocks delivery (job 14's own busy-bypass
#      for `proven_boundary`, AND `deliver_compact_now`'s unconditional
#      "busy is safe to type into" premise, are both removed) — `/compact`
#      is only ever TYPED when the pane is observably at rest right now,
#      so there is no window between "marker looked safe" and "keystrokes
#      actually executed" for the state to have moved on.
#
# A parked request is never lost: it stays in `compact-requests.json`
# (job 14) or falls back to the polled retry (`deliver_compact_now`), and
# fires at the next sweep that finds the session genuinely idle on a
# `✅`/no-marker turn — proven by
# `test_a_held_request_delivers_once_a_genuine_boundary_arrives` below.
# --------------------------------------------------------------------------- #

class TestCompactRequiresGenuineIdleBoundary(unittest.TestCase):
    """#333 — extends #109/#102's marker gate (❓/⏳ block) and #122/#301's
    busy-bypass to apply IDENTICALLY regardless of origin. The historical
    #121/#122/#301 exemptions for `proven_boundary` origins are REVERSED
    here on direct user instruction, with the live evidence that motivated
    the reversal recorded in the section comment above and in the #333
    issue's own design comment."""

    SID = "sess-333"
    CWD = "/home/x/proj333"
    PANE = "%33"
    PROVEN = "subagent-stop"
    SELF = "self-callback"
    WORKING_NO_ASK = "⏳ WORKING: 5 workerov beží — hlásim sa pri dokončení."
    DONE = "✅ DONE: kolo zmergované, backlog pokračuje."

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

    # -- job 14 (compact_ticket_boundary) ------------------------------- #

    def test_job14_holds_a_plain_working_turn_even_for_a_proven_boundary(self):
        # the THIRD occurrence, exactly: no ❓ anywhere, no Work Complete,
        # just an ordinary mid-work progress report — must NOT compact.
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        path = _write_request(self._p(), self.SID, self.CWD, origin=self.PROVEN)
        tmux = CompactFakeTmux(CB_IDLE_CAP)   # pane itself is idle right now
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux.typed_texts(), [], logs)
        self.assertTrue(any("not-a-boundary" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_job14_never_types_into_a_busy_pane_for_a_proven_boundary(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        path = _write_request(self._p(), self.SID, self.CWD, origin=self.PROVEN)
        tmux = CompactFakeTmux(CB_BUSY_CAP)
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, {self.SID: (self.PANE, CB_BUSY_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux.typed_texts(), [], logs)
        self.assertTrue(any("busy" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_job14_never_types_into_a_busy_pane_even_with_grace_elapsed(self):
        # the THIRD occurrence's exact combination: live background tasks,
        # PAST the #250 grace window, busy pane, proven-boundary origin. The
        # old code's grace-elapsed branch fell straight through the busy
        # check for this origin; it must not. Two sweeps, same shape as
        # test_repeated_re_records_do_not_reset_the_grace_anchor above:
        # sweep 1 stamps `deferred_since`; sweep 2 (past grace) must STILL
        # refuse to type, because the pane is busy.
        #
        # #333-review MAJOR-1 -- uses `self.DONE` (a SAFE marker), not
        # `self.WORKING_NO_ASK`: with a ⏳ marker, `_compact_not_at_boundary`
        # ALREADY refuses before this test's busy-check-and-grace-elapsed
        # interaction is ever reached, so the marker gate does the refusing
        # and the busy/grace logic under test is never genuinely exercised
        # -- confirmed by mutation: reverting the busy-check's `and not
        # proven_boundary` exemption (the exact pre-#333 bug) still passed
        # this test unmodified. A safe marker forces the refusal to come
        # from the busy+grace path specifically.
        proj = self._proj()
        path = self._p()
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        t0 = 1_000_000.0
        wd.record_compact_request(self.SID, self.CWD, now=t0, path=path,
                                  origin=self.PROVEN)
        tmux1 = CompactFakeTmux(CB_BUSY_CAP)
        with m.patch.object(wd, "_session_has_live_bg_tasks", return_value=True):
            wd.compact_ticket_boundary(
                t0, tmux1, {}, {self.SID: (self.PANE, CB_BUSY_CAP)},
                path=path, projects_dir=proj)
        self.assertEqual(tmux1.sent, [])

        t1 = t0 + wd.COMPACT_DEFER_GRACE_S + 5
        tmux2 = CompactFakeTmux(CB_BUSY_CAP)
        with m.patch.object(wd, "_session_has_live_bg_tasks", return_value=True):
            logs2 = wd.compact_ticket_boundary(
                t1, tmux2, {}, {self.SID: (self.PANE, CB_BUSY_CAP)},
                path=path, projects_dir=proj)
        self.assertEqual(tmux2.typed_texts(), [], logs2)
        self.assertTrue(any("skip busy" in ln for ln in logs2), logs2)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_a_held_request_delivers_once_a_genuine_boundary_arrives(self):
        # nothing is ever LOST — a held request fires on the very next
        # sweep that finds the session genuinely idle on a safe marker.
        proj = self._proj()
        path = self._p()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        _write_request(path, self.SID, self.CWD, origin=self.PROVEN)
        held_tmux = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(
            time.time(), held_tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(held_tmux.typed_texts(), [])
        self.assertIn(self.SID, wd.load_compact_requests(path))

        # the ticket lands: the session's last real turn is now a genuine
        # `✅ DONE` boundary, pane idle.
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        deliver_tmux = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(
            time.time(), deliver_tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertIn("/compact", deliver_tmux.typed_texts())
        self.assertEqual(wd.load_compact_requests(path), {})

    # -- deliver_compact_now (the synchronous #65 path) ------------------ #

    def test_sync_path_holds_a_plain_working_turn_for_a_proven_boundary(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj, min_context=1,
                                      origin=self.SELF)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(word)

    def test_sync_path_never_types_into_a_busy_pane_for_any_origin(self):
        # #65's own original claim ("busy is safe to type into") is the
        # confirmed root cause of #333's queue-drain race — reversed for
        # EVERY origin, not just non-proven ones.
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_BUSY_CAP)
        word = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj, min_context=1)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(word)

    def test_sync_path_never_types_into_a_busy_pane_for_a_proven_boundary(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_BUSY_CAP)
        word = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj, min_context=1,
                                      origin=self.PROVEN)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(word)

    def test_sync_path_still_delivers_on_a_genuinely_idle_boundary(self):
        # positive control -- the fix narrows WHEN, it does not disable
        # delivery outright.
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj, min_context=1,
                                      origin=self.PROVEN)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", tmux.typed_texts())


# --------------------------------------------------------------------------- #
# #394 (2026-08-11, gatekeeper + dev1 live evidence) -- #333's own
# `_compact_not_at_boundary` marker block has NO grace-elapsed escape for a
# PROVEN-boundary origin, unlike its sibling live-tasks defer (#250) it
# otherwise mirrors exactly. A continuously-busy `/goal` loop (job 14's own
# primary intended consumer, and the entire reason the proven-boundary
# origins exist at all) keeps producing new `⏳` turns (CI waits, the next
# dispatch, a sibling worker) far faster than a session ever settles on a
# genuinely quiet `✅`/no-marker one -- measured live on dev1, 2026-08-11:
# EVERY `origin=subagent-stop` request that day either lapsed at
# `COMPACT_REQUEST_MAX_AGE_S` with the transcript still reading `⏳` at lapse
# time, or was still cycling `skip not-a-boundary` -- ZERO `SEND`s all day
# for that origin, matching the ticket's own gatekeeper journal (a request
# "skip not-a-boundary"-looping every ~70s for 22+ continuous minutes after
# a genuine `## ✅ Work Complete` turn was printed, until the 30-minute
# lapse).
#
# #333's OWN test (`TestCompactRequiresGenuineIdleBoundary`, above) is a
# DELIBERATE, user-directed invariant this fix does NOT reverse: a FRESH
# marker-hold must still block, exactly as before (a plain `⏳ WORKING`
# turn is not license to compact just because the origin is proven). This
# class proves the COMPLEMENT #333 left unfixed: given enough time (past
# `COMPACT_MARKER_HOLD_GRACE_S`), the SAME shape (proven-boundary, marker
# stuck on `⏳`, pane genuinely idle RIGHT NOW) eventually delivers instead
# of just lapsing unfired -- while `kind == "busy"` and the `❓`-only gate
# both stay fully unconditional, so #333's actual incident (a busy-typed
# `/compact` draining several turns later at an arbitrary bad moment)
# cannot recur through this path.
# --------------------------------------------------------------------------- #

class TestCompactMarkerHoldGraceElapsed(unittest.TestCase):
    SID = "sess-394"
    CWD = "/home/x/proj394"
    PANE = "%39"
    PROVEN = "subagent-stop"
    WORKING_NO_ASK = "⏳ WORKING: 5 workerov beží — hlásim sa pri dokončení."
    ASKED = "❓ NEEDS YOU: mergnem #1 alebo #2?"
    DONE = "✅ DONE: kolo zmergované, backlog pokračuje."

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

    def test_marker_hold_grace_never_overrides_while_live_tasks_exist(self):
        # #400 FIX 5(b) -- live gatekeeper incident, 2026-08-12: the
        # marker-hold grace (#394) must NOT deliver a `⏳`-blocked,
        # proven-boundary request once past COMPACT_MARKER_HOLD_GRACE_S if
        # the session STILL has live background tasks -- that is not "a
        # stale marker on an otherwise-idle session", it is genuinely
        # still mid-work. CB_IDLE_WAITING_CAP carries the same
        # "Waiting for N background agents" row `_session_has_live_bg_
        # tasks` signal (a) reads.
        proj = self._proj()
        path = self._p()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        t0 = 1_000_000.0
        wd.record_compact_request(self.SID, self.CWD, now=t0, path=path,
                                  origin=self.PROVEN)

        # sweep 1: fresh hold, live tasks -- refuses (unchanged), stamps
        # not_boundary_since.
        tmux1 = CompactFakeTmux(CB_IDLE_WAITING_CAP)
        logs1 = wd.compact_ticket_boundary(
            t0, tmux1, {}, {self.SID: (self.PANE, CB_IDLE_WAITING_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux1.typed_texts(), [])
        d_mid = wd.load_compact_requests(path)
        self.assertEqual(d_mid[self.SID].get("not_boundary_since"), t0)

        # sweep 2: PAST COMPACT_MARKER_HOLD_GRACE_S, marker still ⏳, and
        # STILL live tasks -- before FIX 5 this delivered anyway
        # ("OK not-a-boundary-grace-elapsed"); now it must keep refusing.
        t1 = t0 + wd.COMPACT_MARKER_HOLD_GRACE_S + 5
        tmux2 = CompactFakeTmux(CB_IDLE_WAITING_CAP)
        logs2 = wd.compact_ticket_boundary(
            t1, tmux2, {}, {self.SID: (self.PANE, CB_IDLE_WAITING_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux2.typed_texts(), [], logs2)
        self.assertTrue(any("skip not-a-boundary" in ln for ln in logs2),
                        logs2)
        self.assertFalse(any("grace-elapsed" in ln for ln in logs2), logs2)
        self.assertIn(self.SID, wd.load_compact_requests(path))

        # sweep 3: live tasks finally clear (a plain CB_IDLE_CAP, no
        # waiting row) -- the SAME already-elapsed grace now genuinely
        # applies and delivers, proving the narrowing gates delivery, not
        # the grace mechanism itself.
        tmux3 = CompactFakeTmux(CB_IDLE_CAP)
        logs3 = wd.compact_ticket_boundary(
            t1 + 1, tmux3, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertIn("/compact", tmux3.typed_texts(), logs3)
        self.assertTrue(any("grace-elapsed" in ln for ln in logs3), logs3)
        self.assertEqual(wd.load_compact_requests(path), {})

    def test_a_proven_boundary_marker_hold_eventually_delivers_once_grace_elapses(self):

        proj = self._proj()
        path = self._p()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        t0 = 1_000_000.0
        wd.record_compact_request(self.SID, self.CWD, now=t0, path=path,
                                  origin=self.PROVEN)

        # sweep 1: a FRESH hold -- must still refuse, exactly like #333's
        # own test (a plain ⏳ WORKING turn, idle pane) -- and must stamp
        # `not_boundary_since` the first time it observes the block.
        tmux1 = CompactFakeTmux(CB_IDLE_CAP)
        logs1 = wd.compact_ticket_boundary(
            t0, tmux1, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux1.typed_texts(), [])
        self.assertTrue(any("not-a-boundary" in ln for ln in logs1), logs1)
        d_mid = wd.load_compact_requests(path)
        self.assertIn("not_boundary_since", d_mid[self.SID])
        self.assertEqual(d_mid[self.SID]["not_boundary_since"], t0)

        # sweep 2: past COMPACT_MARKER_HOLD_GRACE_S, marker STILL ⏳ (the
        # loop never went quiet), pane genuinely idle right now -- must
        # deliver anyway rather than lapsing unfired.
        t1 = t0 + wd.COMPACT_MARKER_HOLD_GRACE_S + 5
        tmux2 = CompactFakeTmux(CB_IDLE_CAP)
        logs2 = wd.compact_ticket_boundary(
            t1, tmux2, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertIn("/compact", tmux2.typed_texts(), logs2)
        self.assertTrue(any("grace-elapsed" in ln for ln in logs2), logs2)
        self.assertEqual(wd.load_compact_requests(path), {})

    def test_a_request_recorded_at_a_real_work_complete_boundary_survives_the_loop_moving_on(self):
        # the ticket's own reported shape, literally: the SubagentStop hook
        # fires at a GENUINE `## ✅ Work Complete` boundary, but by the time
        # job 14 EVER polls, the SAME session has already dispatched the
        # next ticket and its transcript now reads `⏳` -- repeatedly,
        # every ~70s, exactly like the ticket's own "0:0.0 for 22 minutes"
        # journal evidence. The request must not simply die at
        # COMPACT_REQUEST_MAX_AGE_S having delivered nothing.
        proj = self._proj()
        path = self._p()
        work_complete = "## ✅ Work Complete\n\n...\n\n✅ DONE: ticket #41 hotový."
        _write_marker_transcript(proj, self.CWD, self.SID, work_complete)
        t0 = 1_000_000.0
        wd.record_compact_request(self.SID, self.CWD, now=t0, path=path,
                                  origin=self.PROVEN)

        # the loop moves on immediately -- before job 14 ever gets to look,
        # the SAME session's transcript already shows a later, unrelated
        # ⏳ turn, and stays that way for several sweeps in a row.
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        for i in range(3):
            tmux = CompactFakeTmux(CB_IDLE_CAP)
            logs = wd.compact_ticket_boundary(
                t0 + i * 70, tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
                path=path, projects_dir=proj)
            self.assertEqual(tmux.typed_texts(), [], logs)
            self.assertTrue(any("not-a-boundary" in ln for ln in logs), logs)

        # past grace, marker STILL ⏳ -- must deliver rather than lapse.
        t1 = t0 + wd.COMPACT_MARKER_HOLD_GRACE_S + 5
        tmux_final = CompactFakeTmux(CB_IDLE_CAP)
        logs_final = wd.compact_ticket_boundary(
            t1, tmux_final, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertIn("/compact", tmux_final.typed_texts(), logs_final)
        self.assertEqual(wd.load_compact_requests(path), {})

    def test_marker_hold_grace_elapsed_still_refuses_a_busy_pane(self):
        # #333's OWN real incident stays closed: the grace can only ever
        # let a request past the MARKER gate; a busy pane is still
        # unconditionally refused, for every origin, no exception.
        proj = self._proj()
        path = self._p()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        t0 = 1_000_000.0
        wd.record_compact_request(self.SID, self.CWD, now=t0, path=path,
                                  origin=self.PROVEN)
        tmux1 = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(
            t0, tmux1, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)

        t1 = t0 + wd.COMPACT_MARKER_HOLD_GRACE_S + 5
        tmux2 = CompactFakeTmux(CB_BUSY_CAP)
        logs2 = wd.compact_ticket_boundary(
            t1, tmux2, {}, {self.SID: (self.PANE, CB_BUSY_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux2.typed_texts(), [], logs2)
        self.assertTrue(any("skip busy" in ln for ln in logs2), logs2)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_marker_hold_grace_never_overrides_a_pending_question(self):
        # ❓ is a genuinely different marker, unconditional, no grace, at
        # any age -- it is caught by an EARLIER, separate gate
        # (`_compact_blocked_by_question`) this fix does not touch at all,
        # so by construction the marker-hold-grace override can only ever
        # engage against `⏳`.
        proj = self._proj()
        path = self._p()
        _write_marker_transcript(proj, self.CWD, self.SID, self.ASKED)
        t0 = 1_000_000.0
        wd.record_compact_request(self.SID, self.CWD, now=t0, path=path,
                                  origin=self.PROVEN)
        t1 = t0 + wd.COMPACT_MARKER_HOLD_GRACE_S + 5
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(
            t1, tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux.typed_texts(), [], logs)
        self.assertTrue(any("blocked-question" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_a_blank_origin_request_gets_no_grace_at_all(self):
        # only a PROVEN-boundary origin gets this override -- #301's own
        # precedent for the sibling #99/#48 gates, applied identically
        # here. A blank-origin (plain Stop-hook) request has no independent
        # proof a boundary ever existed, so it must STILL refuse at the
        # exact instant that would deliver a proven-boundary request.
        proj = self._proj()
        path = self._p()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        t0 = 1_000_000.0
        wd.record_compact_request(self.SID, self.CWD, now=t0, path=path)
        tmux1 = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(
            t0, tmux1, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)

        t1 = t0 + wd.COMPACT_MARKER_HOLD_GRACE_S + 5
        tmux2 = CompactFakeTmux(CB_IDLE_CAP)
        logs2 = wd.compact_ticket_boundary(
            t1, tmux2, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux2.typed_texts(), [], logs2)
        self.assertTrue(any("not-a-boundary" in ln for ln in logs2), logs2)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_a_new_hold_episode_after_the_marker_recovers_gets_its_own_fresh_grace(self):
        # #394-review F1 -- `not_boundary_since` must be CLEARED the moment
        # the block itself clears (the marker genuinely reads a boundary
        # again), even on a sweep that still doesn't deliver for an
        # UNRELATED reason (here: the pane happens to be busy). Otherwise a
        # LATER, genuinely NEW hold (the transcript goes back to ⏳)
        # silently inherits the OLD, already-elapsed anchor and delivers on
        # a hold that has barely started -- exactly the "compact a plain ⏳
        # WORKING turn" shape #333 exists to forbid. Reproduced live against
        # the pre-fix code by the adversarial review's own repro script
        # before this test was written.
        proj = self._proj()
        path = self._p()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        t0 = 1_000_000.0
        wd.record_compact_request(self.SID, self.CWD, now=t0, path=path,
                                  origin=self.PROVEN)

        # sweep 1: a fresh hold -- refuse, stamp the anchor at t0.
        tmux1 = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(
            t0, tmux1, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(
            wd.load_compact_requests(path)[self.SID]["not_boundary_since"], t0)

        # sweep 2: the marker genuinely recovers (a real ✅ boundary again)
        # but the pane happens to be busy right now -- the request survives
        # (never consumed by the busy skip), but the STALE anchor must be
        # cleared, since the hold episode it belonged to just ended.
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        t1 = t0 + wd.COMPACT_MARKER_HOLD_GRACE_S + 100
        tmux2 = CompactFakeTmux(CB_BUSY_CAP)
        logs2 = wd.compact_ticket_boundary(
            t1, tmux2, {}, {self.SID: (self.PANE, CB_BUSY_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux2.typed_texts(), [], logs2)
        self.assertTrue(any("skip busy" in ln for ln in logs2), logs2)
        self.assertNotIn("not_boundary_since",
                         wd.load_compact_requests(path)[self.SID])

        # sweep 3: a genuinely NEW hold starts (⏳ again) -- it must get its
        # OWN fresh grace window, never the stale, already-elapsed one from
        # sweep 1's episode.
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING_NO_ASK)
        t2 = t1 + 1
        tmux3 = CompactFakeTmux(CB_IDLE_CAP)
        logs3 = wd.compact_ticket_boundary(
            t2, tmux3, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux3.typed_texts(), [], logs3)
        self.assertTrue(any("skip not-a-boundary" in ln for ln in logs3), logs3)
        self.assertEqual(
            wd.load_compact_requests(path)[self.SID]["not_boundary_since"], t2)


class TestCompactMarkerHoldGraceRelationship(unittest.TestCase):
    """#394 -- mirrors TestCompactDeferGraceRelationship exactly: the grace
    window MUST stay well under the request TTL, or a request would LAPSE
    (dropped, unfired) before its own grace window ever has a chance to
    elapse -- recreating the exact starvation this fix exists to kill."""

    def test_grace_stays_comfortably_under_the_request_ttl(self):
        self.assertLess(wd.COMPACT_MARKER_HOLD_GRACE_S, wd.COMPACT_REQUEST_MAX_AGE_S)


class TestCompactMarkerHoldInGrace(unittest.TestCase):
    """#394 -- `_compact_marker_hold_in_grace`, mirrors
    `TestCompactLiveTasksInGrace` exactly (strict `<`, unmeasurable stays
    in-grace)."""

    def test_within_grace_is_true(self):
        self.assertTrue(wd._compact_marker_hold_in_grace(
            1000, 1000 + wd.COMPACT_MARKER_HOLD_GRACE_S - 1))

    def test_exactly_at_grace_is_no_longer_in_grace(self):
        self.assertFalse(wd._compact_marker_hold_in_grace(
            1000, 1000 + wd.COMPACT_MARKER_HOLD_GRACE_S))

    def test_past_grace_is_false(self):
        self.assertFalse(wd._compact_marker_hold_in_grace(
            1000, 1000 + wd.COMPACT_MARKER_HOLD_GRACE_S + 1))

    def test_unmeasurable_age_stays_in_grace(self):
        self.assertTrue(wd._compact_marker_hold_in_grace(None, 1000))
        self.assertTrue(wd._compact_marker_hold_in_grace("garbage", 1000))

    def test_env_override_is_honored(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MARKER_HOLD_GRACE_S": "10"}):
            self.assertFalse(wd._compact_marker_hold_in_grace(1000, 1015))
            self.assertTrue(wd._compact_marker_hold_in_grace(1000, 1005))

    def test_explicit_grace_param_overrides_env_and_default(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MARKER_HOLD_GRACE_S": "9999"}):
            self.assertFalse(
                wd._compact_marker_hold_in_grace(1000, 1050, grace=10))


class TestCompactMarkerHoldGraceEnvClamp(unittest.TestCase):
    """#394-review F2 -- mirrors `TestCompactLiveTasksInGrace`'s own env-clamp
    tests exactly, for the sibling `_compact_marker_hold_grace`: mutating away
    its ENTIRE clamp body (`return raw`) passed the whole suite before these
    existed -- the adversarial review's own mutation. A misconfigured env
    override must never disable the override outright (0/negative) or
    recreate the lapse-before-grace starvation (>= the request TTL)."""

    def test_env_override_negative_is_clamped_to_a_minimum(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MARKER_HOLD_GRACE_S": "-100"}):
            self.assertEqual(wd._compact_marker_hold_grace(), 1)

    def test_env_override_zero_is_clamped_to_a_minimum(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MARKER_HOLD_GRACE_S": "0"}):
            self.assertEqual(wd._compact_marker_hold_grace(), 1)

    def test_env_override_non_numeric_falls_back_to_the_default(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MARKER_HOLD_GRACE_S": "garbage"}):
            self.assertEqual(wd._compact_marker_hold_grace(), wd.COMPACT_MARKER_HOLD_GRACE_S)

    def test_env_override_at_or_above_ttl_is_clamped_below_it(self):
        huge = str(wd.COMPACT_REQUEST_MAX_AGE_S + 1000)
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MARKER_HOLD_GRACE_S": huge}):
            self.assertLess(
                wd._compact_marker_hold_grace(), wd.COMPACT_REQUEST_MAX_AGE_S)

    def test_env_override_exactly_at_ttl_is_clamped_below_it(self):
        at_ttl = str(wd.COMPACT_REQUEST_MAX_AGE_S)
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MARKER_HOLD_GRACE_S": at_ttl}):
            self.assertLess(
                wd._compact_marker_hold_grace(), wd.COMPACT_REQUEST_MAX_AGE_S)

    def test_a_sane_env_override_is_returned_unclamped(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MARKER_HOLD_GRACE_S": "600"}):
            self.assertEqual(wd._compact_marker_hold_grace(), 600)

    def test_explicit_grace_param_is_never_clamped(self):
        # every production call leaves grace=None; an explicit override
        # (test/caller-only) is returned verbatim, even outside the sane
        # range -- the clamp protects only the env/const-derived default.
        self.assertEqual(wd._compact_marker_hold_grace(grace=-5), -5)
        self.assertEqual(
            wd._compact_marker_hold_grace(grace=wd.COMPACT_REQUEST_MAX_AGE_S + 5),
            wd.COMPACT_REQUEST_MAX_AGE_S + 5)


class TestNotBoundarySincePreservedAcrossReRecord(unittest.TestCase):
    """#394 -- mirrors TestDeferredSincePreservedAcrossReRecord exactly:
    `not_boundary_since` (job 14's marker-hold grace anchor) must survive a
    re-record for the SAME still-pending session, UNCONDITIONALLY -- a
    session completing tickets faster than the grace window (the exact
    population this fix exists for) would otherwise reset its own anchor
    on every re-record and never actually reach it."""

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def test_not_boundary_since_survives_a_re_record(self):
        p = self._p()
        wd.record_compact_request("n1", "/x", now=100, path=p)
        d = wd.load_compact_requests(p)
        d["n1"]["not_boundary_since"] = 50.0   # simulate job 14's own stamp
        p.write_text(json.dumps(d))
        wd.record_compact_request("n1", "/y", now=500, path=p)   # re-record
        d2 = wd.load_compact_requests(p)
        self.assertEqual(d2["n1"]["not_boundary_since"], 50.0)
        self.assertEqual(d2["n1"]["ts"], 500)
        self.assertEqual(d2["n1"]["cwd"], "/y")

    def test_no_prior_not_boundary_since_means_none_is_invented(self):
        p = self._p()
        wd.record_compact_request("n2", "/x", now=100, path=p)
        wd.record_compact_request("n2", "/y", now=200, path=p)
        d = wd.load_compact_requests(p)
        self.assertNotIn("not_boundary_since", d["n2"])

    def test_a_fresh_session_never_carries_a_stale_not_boundary_since(self):
        p = self._p()
        wd.record_compact_request("n3", "/x", now=100, path=p)
        d = wd.load_compact_requests(p)
        d["n3"]["not_boundary_since"] = 50.0
        p.write_text(json.dumps(d))
        wd.clear_compact_request("n3", path=p)   # delivered/dropped/expired
        wd.record_compact_request("n3", "/z", now=9000, path=p)
        d2 = wd.load_compact_requests(p)
        self.assertNotIn("not_boundary_since", d2["n3"])


# --------------------------------------------------------------------------- #
# #333-review MAJOR-2 (adversarial review, fable) -- the busy-check above
# reads a SWEEP-TOP (job 14) or CALL-TOP (deliver_compact_now) snapshot that
# can be several tmux round-trips stale by the time control actually reaches
# the send: every gate above it (marker re-read, #99/#48 substantiality,
# #246 live-tasks, a git subprocess) spends real wall-clock time first. This
# class proves the fix genuinely re-verifies against a FRESH capture
# immediately before typing, not just at the top of the check chain --
# mirroring `_goal_template_drift`/job 20's own established discipline for
# the identical race (#176-F3/#266).
# --------------------------------------------------------------------------- #

class TestCompactRefusesAPaneThatRacedSinceTheSweep(unittest.TestCase):
    SID = "sess-333-race"
    CWD = "/home/x/proj333race"
    PANE = "%34"
    PROVEN = "subagent-stop"
    DONE = "✅ DONE: kolo zmergované."

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

    def test_job14_refuses_a_pane_that_went_busy_between_sweep_and_send(self):
        # the sweep-top `captured` (fed via panes_by_sid, same as production)
        # reads IDLE -- every check up to the busy-skip passes -- but the
        # FIRST real tmux capture-pane call job 14 issues (this fix's own
        # fresh pre-send re-verify) now reports BUSY.
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        path = _write_request(self._p(), self.SID, self.CWD, origin=self.PROVEN)
        tmux = CompactFakeTmux(CB_IDLE_CAP, cap_seq=[CB_BUSY_CAP])
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertEqual(tmux.typed_texts(), [], logs)
        self.assertTrue(any("skip raced" in ln for ln in logs), logs)
        # a pre-send refusal is never consumed -- the next sweep retries
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_job14_still_delivers_when_the_fresh_recheck_confirms_idle(self):
        # positive control -- the fresh re-check is not a blanket new skip,
        # it only refuses when the pane GENUINELY moved on.
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        path = _write_request(self._p(), self.SID, self.CWD, origin=self.PROVEN)
        tmux = CompactFakeTmux(CB_IDLE_CAP, cap_seq=[CB_IDLE_CAP])
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertIn("/compact", tmux.typed_texts(), logs)
        self.assertEqual(wd.load_compact_requests(path), {})

    def test_sync_path_refuses_a_pane_that_went_busy_between_resolve_and_send(self):
        # cap_seq[0] serves deliver_compact_now's OWN top-of-call resolve
        # (`captured = capture_pane(...)`, reading idle); cap_seq[1] serves
        # this fix's fresh pre-send re-verify, reporting the pane has since
        # gone busy.
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP,
            cap_seq=[CB_IDLE_CAP, CB_BUSY_CAP])
        word = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj, min_context=1,
                                      origin=self.PROVEN)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(word)

    def test_sync_path_still_delivers_when_the_fresh_recheck_confirms_idle(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.DONE)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP,
            cap_seq=[CB_IDLE_CAP, CB_IDLE_CAP])
        word = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj, min_context=1,
                                      origin=self.PROVEN)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", tmux.typed_texts())


if __name__ == "__main__":
    unittest.main()
