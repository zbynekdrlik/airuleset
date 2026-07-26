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
           delivered_path=None):
        path = path or self._p()
        if seed:
            wd.record_compact_request(self.SID, "/home/x/proj", path=path,
                                      msg_hash=msg_hash)
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

    def test_success_removes_the_request_dedup(self):
        tmux, logs, path, _ = self._go(CB_IDLE_CAP)
        self.assertEqual(wd.load_compact_requests(path), {})

    def test_busy_pane_is_skipped_and_request_kept_for_retry(self):
        tmux, logs, path, _ = self._go(CB_BUSY_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("busy" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

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


class TestCompactTicketBoundaryContextThreshold(unittest.TestCase):
    SID = "sess-ctx-1"
    CWD = "/home/x/ctxproj"
    PANE = "%7"

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
# 2c. run_once wiring — job 14 fires ONLY when compact_requests_path is given
# --------------------------------------------------------------------------- #

class RunOnceCompactRequestWiring(unittest.TestCase):
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
        dcn.assert_called_once_with("sid-cli", "/x")
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


if __name__ == "__main__":
    unittest.main()
