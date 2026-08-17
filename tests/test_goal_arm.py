"""The collapsed `/goal` arming callback model (#403).

Mirrors `tests/test_compact.py`'s own contract-locking shape for the
sibling #402 collapse — `watchdog/goal.py`'s own module docstring is the
single source of truth for the design this file locks:

  - Exactly ONE origin ever creates a request (`goal-arm --self`, called by
    the `/autopilot` skill's own Step 2 right after printing the `/goal`
    line) — nothing guessed from a viewport scan, no "virgin candidate"
    heuristic.
  - `deliver_goal()`'s conditions (owner kill-switch, hard non-refreshable
    age cap with a deduped ping on expiry, pane resolve, copy-mode,
    dialog-open, the #170 clear-suppression guard, a tri-state
    already-armed check, boundary classify) are evaluated in order and
    every SKIP leaves the request pending for the next sweep.
  - `goal_sweep` (job 9's new body) re-evaluates every still-pending
    request through that SAME function, clearing only on a TERMINAL word.
  - `goal_dark_watch` (job 20, half 1) NEVER types a keystroke — a
    2-sweep-debounced Discord ping only, and only for the transcript-says-
    armed/footer-says-dark mismatch; a `cleared` marker or no marker at
    all stays silent by construction.
  - `goal_lane_occupancy_nudge` (job 20, half 2) is the ONE remaining
    watchdog-INITIATED keystroke action, and is the only one of the two
    that DOES apply the recent-human-activity gate (arm delivery
    deliberately does not — see the module's own header docstring).
  - The kill-switch (`~/.claude/watchdog-disable-goal`) is honoured by
    every entry point.
"""

import json
import os
import sys
import types
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd
from watchdog import goal


from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    GOAL_BUSY_CAP,
    GOAL_DIALOG_CAP,
    GOAL_DRAFT_CAP,
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _encode,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

class GoalRequestState(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "goal-requests.json")

    def test_record_and_load(self):
        p = self._p()
        self.assertTrue(goal.record_goal_request(
            "sess-1", "/home/x/proj", "/goal STOP...", "full", now=1000,
            path=p, origin="self-callback"))
        d = goal.load_goal_requests(p)
        self.assertEqual(d["sess-1"]["cwd"], "/home/x/proj")
        self.assertEqual(d["sess-1"]["text"], "/goal STOP...")
        self.assertEqual(d["sess-1"]["authority"], "full")
        self.assertEqual(d["sess-1"]["ts"], 1000)
        self.assertEqual(d["sess-1"]["origin"], "self-callback")

    def test_missing_session_is_rejected(self):
        p = self._p()
        self.assertFalse(goal.record_goal_request("", "/x", "/goal x", "full", path=p))
        self.assertEqual(goal.load_goal_requests(p), {})

    def test_ts_is_never_refreshed_across_a_re_record(self):
        # #400's own invariant, mirrored for goal-arm requests: the age-cap
        # anchor is set ONCE, never refreshed by a later re-record for the
        # same still-pending session.
        p = self._p()
        goal.record_goal_request("sess-1", "/x", "/goal a", "full", now=1000,
                                 path=p, origin="self-callback")
        goal.record_goal_request("sess-1", "/y", "/goal b", "branch-merge",
                                 now=5000, path=p, origin="self-callback")
        d = goal.load_goal_requests(p)
        self.assertEqual(d["sess-1"]["ts"], 1000)          # frozen at first-seen
        self.assertEqual(d["sess-1"]["cwd"], "/y")          # latest cwd wins
        self.assertEqual(d["sess-1"]["text"], "/goal b")    # latest text wins
        self.assertEqual(d["sess-1"]["authority"], "branch-merge")

    def test_dark_rearm_never_clobbers_a_pending_self_callback(self):
        # #478 adversarial-review MAJOR — record_goal_request now has TWO
        # writers. A watchdog dark-rearm must NEVER overwrite a still-pending
        # user (self-callback) arm: the user's own explicit /goal request
        # stands entirely intact (origin, text, authority, ts).
        p = self._p()
        goal.record_goal_request("sess-1", "/user/cwd", "/goal USER", "full",
                                 now=1000, path=p,
                                 origin="self-callback")
        ok = goal.record_goal_request("sess-1", "/wd/cwd", "/goal WATCHDOG",
                                      "branch-merge", now=2000, path=p,
                                      origin="dark-rearm")
        self.assertTrue(ok, "a refused downgrade is a successful no-op")
        e = goal.load_goal_requests(p)["sess-1"]
        self.assertEqual(e["origin"], "self-callback")
        self.assertEqual(e["text"], "/goal USER")
        self.assertEqual(e["authority"], "full")
        self.assertEqual(e["ts"], 1000)

    def test_user_callback_upgrades_a_pending_dark_rearm_with_a_fresh_ts(self):
        # #478 adversarial-review MAJOR (path 2) — a genuine user callback
        # landing on a pending dark-rearm entry takes the user's fresh values
        # AND a FRESH ts anchor, so the user's brand-new arm is never judged
        # expired against the stale watchdog anchor.
        p = self._p()
        goal.record_goal_request("sess-1", "/wd/cwd", "/goal WATCHDOG", "full",
                                 now=1000, path=p, origin="dark-rearm")
        goal.record_goal_request("sess-1", "/user/cwd", "/goal USER", "full",
                                 now=9000, path=p, origin="self-callback")
        e = goal.load_goal_requests(p)["sess-1"]
        self.assertEqual(e["origin"], "self-callback")
        self.assertEqual(e["text"], "/goal USER")
        self.assertEqual(e["ts"], 9000, "user upgrade resets the ts anchor")

    def test_dark_rearm_re_record_preserves_ts_same_origin(self):
        # A same-origin dark-rearm re-record (the backoff/cap path) preserves
        # the non-refreshable #400 anchor exactly like self-callback does —
        # the downgrade guard must not accidentally reset it.
        p = self._p()
        goal.record_goal_request("sess-1", "/wd", "/goal a", "full", now=1000,
                                 path=p, origin="dark-rearm")
        goal.record_goal_request("sess-1", "/wd", "/goal a", "full", now=5000,
                                 path=p, origin="dark-rearm")
        self.assertEqual(goal.load_goal_requests(p)["sess-1"]["ts"], 1000)

    def test_clear_removes_one_request_only(self):
        p = self._p()
        goal.record_goal_request("sess-1", "/x", "/goal a", "full", path=p)
        goal.record_goal_request("sess-2", "/y", "/goal b", "full", path=p)
        self.assertTrue(goal.clear_goal_request("sess-1", path=p))
        d = goal.load_goal_requests(p)
        self.assertNotIn("sess-1", d)
        self.assertIn("sess-2", d)

    def test_clear_absent_session_returns_false(self):
        p = self._p()
        self.assertFalse(goal.clear_goal_request("nope", path=p))

    def test_load_bad_file_is_empty(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertEqual(goal.load_goal_requests(p), {})

    def test_load_missing_file_is_empty(self):
        p = self._p()
        self.assertEqual(goal.load_goal_requests(p), {})

    def test_goal_requests_path_resolved_at_call_time(self):
        with m.patch.dict(os.environ, {"HOME": "/tmp/fake-home-goal"}):
            self.assertEqual(
                goal.goal_requests_path(),
                Path("/tmp/fake-home-goal") / ".claude" / "goal-requests.json")


# --------------------------------------------------------------------------- #
# 2. Template resolution — the #403-review greedy-DOTALL regex fix, the
#    4000-char cap refusal, missing-authority handling.
# --------------------------------------------------------------------------- #

def _skill_md(*blocks):
    """Build a minimal SKILL.md carrying one or more
    `**AUTHORITY: X**` + fenced `/goal ...` blocks, in the exact shape
    `_GOAL_AUTHORITY_BLOCK_RX` anchors on."""
    parts = ["## Step 2 — Start the engine\n"]
    for authority, line in blocks:
        parts.append("**AUTHORITY: %s**\n\n```\n%s\n```\n\n" % (authority, line))
    return "\n".join(parts)


class GoalTemplateResolution(unittest.TestCase):
    def _p(self, content):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "SKILL.md"
        p.write_text(content, encoding="utf-8")
        return str(p)

    def test_resolves_the_matching_authority_block(self):
        p = self._p(_skill_md(
            ("full", "/goal STOP CONDITIONS full text"),
            ("branch-merge", "/goal STOP CONDITIONS branch text"),
            ("fork-no-merge", "/goal STOP CONDITIONS fork text")))
        self.assertEqual(goal.goal_template_for_authority("full", path=p),
                         "/goal STOP CONDITIONS full text")
        self.assertEqual(goal.goal_template_for_authority("branch-merge", path=p),
                         "/goal STOP CONDITIONS branch text")
        self.assertEqual(goal.goal_template_for_authority("fork-no-merge", path=p),
                         "/goal STOP CONDITIONS fork text")

    def test_three_templates_never_bleed_into_one_giant_capture(self):
        # #403-review CRITICAL: a greedy DOTALL `.+` in the goal-line group
        # backtracks from the END of the file, so the FIRST authority
        # block ("full") used to swallow all three templates as one
        # oversized capture -- every authority silently resolved to None
        # (correctly refused by the cap, but for the WRONG reason: the
        # feature was permanently non-functional). Each resolved line here
        # must be reasonably short and must NOT contain either sibling
        # template's own distinguishing word.
        p = self._p(_skill_md(
            ("full", "/goal STOP CONDITIONS full text mentions main only"),
            ("branch-merge", "/goal STOP CONDITIONS branch text mentions integration only"),
            ("fork-no-merge", "/goal STOP CONDITIONS fork text mentions handoff only")))
        full = goal.goal_template_for_authority("full", path=p)
        self.assertNotIn("integration", full)
        self.assertNotIn("handoff", full)
        self.assertLess(len(full), 200)

    def test_missing_authority_returns_none(self):
        p = self._p(_skill_md(("full", "/goal x")))
        self.assertIsNone(goal.goal_template_for_authority("branch-merge", path=p))

    def test_blank_authority_returns_none(self):
        self.assertIsNone(goal.goal_template_for_authority(""))
        self.assertIsNone(goal.goal_template_for_authority(None))

    def test_unreadable_file_returns_none(self):
        self.assertIsNone(goal.goal_template_for_authority(
            "full", path="/nonexistent/SKILL.md"))

    def test_over_cap_line_is_refused_never_typed(self):
        # #169 -- a template Claude Code itself would reject must never be
        # resolved as armable at all.
        long_line = "/goal " + ("x" * (goal.GOAL_ARM_CHAR_CAP + 50))
        p = self._p(_skill_md(("full", long_line)))
        self.assertIsNone(goal.goal_template_for_authority("full", path=p))

    def test_the_real_shipped_skill_md_resolves_all_three_authorities(self):
        # Live-shipped-artifact proof, not just a synthetic fixture: the
        # ACTUAL installed skills/autopilot/SKILL.md this repo ships must
        # resolve for real, under the cap, for all three profiles.
        real = str(Path(__file__).resolve().parent.parent
                   / "skills" / "autopilot" / "SKILL.md")
        for authority in ("full", "branch-merge", "fork-no-merge"):
            line = goal.goal_template_for_authority(authority, path=real)
            self.assertIsNotNone(line, "authority %r failed to resolve "
                                 "from the real shipped SKILL.md" % authority)
            self.assertTrue(line.startswith("/goal "))
            self.assertLessEqual(len(line), goal.GOAL_ARM_CHAR_CAP)


# --------------------------------------------------------------------------- #
# 3. deliver_goal — the state machine.
# --------------------------------------------------------------------------- #

class TestDeliverGoal(unittest.TestCase):
    SID = "sess-goal-deliver-1"
    CWD = "/home/newlevel/devel/goaltest"
    TEXT = "/goal STOP CONDITIONS test payload"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, captured, now=None, request_ts=None, in_mode=False,
           cap_seq=(), proj=None, state=None, send_fn=None, dry_run=False,
           model_type=True, origin=None):
        if proj is None:
            proj = self._dir()
            _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured, in_mode=in_mode,
            cap_seq=cap_seq, model_type=model_type)
        # `origin` only forwarded when set, so the pre-#478 callers exercise
        # the production default (origin=None -> no recent-human gate).
        kw = {} if origin is None else {"origin": origin}
        word = goal.deliver_goal(self.SID, self.CWD, self.TEXT, "full",
                                 run=tmux, projects_dir=proj, now=now,
                                 state=state, request_ts=request_ts,
                                 send_fn=send_fn, dry_run=dry_run,
                                 sleep_fn=lambda s: None, **kw)
        return word, tmux, proj

    def test_idle_bare_pane_sends(self):
        word, tmux, _ = self._go(GOAL_IDLE_CAP)
        self.assertEqual(word, "sent")
        self.assertIn(self.TEXT, tmux.typed_texts())

    def test_no_pane_skips(self):
        proj = self._dir()      # no transcript, no matching pane
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        word = goal.deliver_goal("no-such-sid", self.CWD, self.TEXT, "full",
                                 run=tmux, projects_dir=proj)
        self.assertEqual(word, "skip:no-pane")
        self.assertEqual(tmux.sent, [])

    def test_pane_in_copy_mode_skips(self):
        word, tmux, _ = self._go(GOAL_IDLE_CAP, in_mode=True)
        self.assertEqual(word, "skip:in-mode")
        self.assertEqual(tmux.sent, [])

    def test_dialog_open_skips(self):
        word, tmux, _ = self._go(GOAL_DIALOG_CAP)
        self.assertEqual(word, "skip:dialog-open")
        self.assertEqual(tmux.sent, [])

    def test_busy_pane_skips(self):
        # `pane_goal_armed` runs BEFORE the boundary classify inside
        # `deliver_goal` -- a genuinely busy capture (no `❯` line anywhere)
        # is UNDETERMINABLE for it (no box to read), so the real code path
        # reaches "skip:undeterminable" here, never "skip:busy" (that kind
        # IS a real, separately-reachable branch -- exercised below by
        # forcing `pane_goal_armed` to a determinable False so the busy-
        # kind check itself is what's under test, isolated from that
        # ordering interaction).
        word, tmux, _ = self._go(GOAL_BUSY_CAP)
        self.assertEqual(word, "skip:undeterminable")
        self.assertEqual(tmux.sent, [])

    def test_busy_kind_itself_skips_once_armed_is_determinable(self):
        with m.patch.object(wd, "pane_goal_armed", return_value=False):
            word, tmux, _ = self._go(GOAL_BUSY_CAP)
        self.assertEqual(word, "skip:busy")
        self.assertEqual(tmux.sent, [])

    def test_already_armed_drops(self):
        word, tmux, _ = self._go(GOAL_ARMED_CAP)
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(tmux.sent, [])

    def test_stale_dark_rearm_request_is_dropped_at_delivery(self):
        # #524 -- a dark-rearm decision goes stale FAST (recorded from a dark
        # READ; the loop's state changes within a sweep or two). A dark-rearm
        # request older than GOAL_DARK_REARM_STALE_S is DROPPED at delivery,
        # never typed late (the H1 "stale request delivered late" concern,
        # closed on the ONE origin that can spontaneously TYPE).
        now = 1_000_000
        old_ts = now - goal.GOAL_DARK_REARM_STALE_S - 1
        word, tmux, _ = self._go(GOAL_IDLE_CAP, now=now, request_ts=old_ts,
                                 origin=goal._GOAL_REARM_ORIGIN)
        self.assertEqual(word, "drop:stale-rearm")
        self.assertEqual(tmux.sent, [])

    def test_fresh_dark_rearm_request_still_delivers(self):
        # The freshness gate is TIGHT, not a blanket refusal: a just-recorded
        # dark-rearm still types.
        now = 1_000_000
        word, tmux, _ = self._go(GOAL_IDLE_CAP, now=now, request_ts=now - 10,
                                 origin=goal._GOAL_REARM_ORIGIN)
        self.assertEqual(word, "sent")

    def test_dark_rearm_freshness_gate_is_origin_scoped(self):
        # A NON-rearm origin at the SAME stale age is NOT dropped by this gate
        # (only the 30-min generic age cap governs it) -- the tight staleness
        # is dark-rearm-ONLY, never applied to a user self-callback arm.
        now = 1_000_000
        old_ts = now - goal.GOAL_DARK_REARM_STALE_S - 1   # < 30-min generic cap
        word, tmux, _ = self._go(GOAL_IDLE_CAP, now=now, request_ts=old_ts,
                                 origin="self-callback")
        self.assertEqual(word, "sent")

    def test_draft_pane_delivers_via_stash(self):
        # A draft in the box must be delivered through deliver_with_stash,
        # never a raw overwrite -- asserted against the REAL call this
        # time (#403-review m1: the original version accepted EITHER
        # "sent" or "skip:stash-abort" with no keystrokes at all,  so
        # neutering the whole draft branch to an unconditional no-op still
        # passed it -- the primitive was never actually proven to run).
        calls = []

        def _fake_stash(pid, text, run, **kw):
            calls.append((pid, text, kw.get("captured")))
            return True

        with m.patch.object(wd, "deliver_with_stash", side_effect=_fake_stash):
            word, tmux, _ = self._go(GOAL_DRAFT_CAP)
        self.assertEqual(word, "sent")
        self.assertEqual(len(calls), 1)
        pid, text, captured = calls[0]
        self.assertEqual(text, self.TEXT)
        self.assertEqual(captured, GOAL_DRAFT_CAP)

    def test_stash_send_marks_janitor_watch_before_attempt_and_clears_on_success(self):
        # #403-review MAJOR M1: the stash branch used to mark provenance
        # only AFTER a successful send and never clear it -- backwards vs.
        # the bare-box branch just below, which marks BEFORE attempting
        # (so a stuck send stays recoverable by the shared #372 janitor)
        # and clears only once success is verified.
        state = {}
        with m.patch.object(wd, "deliver_with_stash", return_value=True):
            word, tmux, _ = self._go(GOAL_DRAFT_CAP, state=state)
        self.assertEqual(word, "sent")
        self.assertNotIn("%9", state.get("janitor_watch", {}))

    def test_stash_abort_leaves_janitor_watch_marked_for_recovery(self):
        state = {}
        with m.patch.object(wd, "deliver_with_stash", return_value=False):
            word, tmux, _ = self._go(GOAL_DRAFT_CAP, state=state)
        self.assertEqual(word, "skip:stash-abort")
        self.assertIn("%9", state.get("janitor_watch", {}))

    def test_stash_attempt_is_marked_before_the_call_not_just_after(self):
        # #403-review round-2 MINOR 1: the two janitor-watch tests above
        # only assert END states (marked-after-failure, cleared-after-
        # success) -- a mutant that moves the mark to AFTER
        # deliver_with_stash (failure-only, mirroring the pre-fix bug's
        # own shape) reaches the IDENTICAL end states and survives both.
        # Assert the mark is ALREADY present the moment deliver_with_stash
        # is entered -- the actual property "mark BEFORE the attempt"
        # exists to guarantee (a crash/hang mid-call must still be
        # recoverable, which an after-the-fact mark cannot provide).
        state = {}
        seen_marked_at_call_time = []

        def _fake_stash(pid, text, run, **kw):
            seen_marked_at_call_time.append(pid in state.get("janitor_watch", {}))
            return True

        with m.patch.object(wd, "deliver_with_stash", side_effect=_fake_stash):
            word, tmux, _ = self._go(GOAL_DRAFT_CAP, state=state)
        self.assertEqual(word, "sent")
        self.assertEqual(seen_marked_at_call_time, [True])

    def test_stash_branch_threads_state_to_deliver_with_stash(self):
        # #488: the durable park record is written/cleared INSIDE
        # deliver_with_stash, and ONLY on a definitively-ours STASH_PARKED
        # (never a pre-existing foreign slot -- review MAJOR). deliver_goal's
        # only job is to THREAD `state` through so that machinery can run; the
        # record write/clear itself is proven at the deliver_with_stash level
        # (test_stash_unconditional.py::Issue488DurableParkRecord).
        state = {"tag": "sentinel"}
        seen = []

        def _fake_stash(pid, text, run, **kw):
            seen.append(kw.get("state"))
            return False

        with m.patch.object(wd, "deliver_with_stash", side_effect=_fake_stash):
            word, tmux, _ = self._go(GOAL_DRAFT_CAP, state=state)
        self.assertEqual(word, "skip:stash-abort")
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0], state)

    def test_hard_age_cap_expires_and_pings_once(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        sent = []
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        now = 1000 + goal.GOAL_REQUEST_MAX_AGE_S + 1
        word = goal.deliver_goal(self.SID, self.CWD, self.TEXT, "full",
                                 run=tmux, projects_dir=proj, now=now,
                                 request_ts=1000,
                                 send_fn=lambda msg, **kw: sent.append(msg))
        self.assertEqual(word, "expired")
        self.assertEqual(tmux.sent, [])          # never typed
        self.assertEqual(len(sent), 1)           # pinged exactly once

    def test_clear_suppression_drops_when_cleared_postdates_the_request(self):
        # #170's own invariant, reused here for arm delivery: a `cleared`
        # marker NEWER than the request means the user deliberately turned
        # the loop off after this request was made -- never re-arm.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_goal_marker(proj, self.CWD, self.SID,
                           "Goal cleared: some earlier text", ts_epoch=2000)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        word = goal.deliver_goal(self.SID, self.CWD, self.TEXT, "full",
                                 run=tmux, projects_dir=proj, now=2100,
                                 request_ts=1000)
        self.assertEqual(word, "drop:cleared-after-request")
        self.assertEqual(tmux.sent, [])

    def test_clear_predating_the_request_does_not_suppress(self):
        # The mirror-image case: a clear that happened BEFORE this request
        # was made is not a reason to refuse it -- the user asked again.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_goal_marker(proj, self.CWD, self.SID,
                           "Goal cleared: some earlier text", ts_epoch=500)
        word, tmux, _ = self._go(GOAL_IDLE_CAP, now=2100, request_ts=1000,
                                 proj=proj)
        self.assertEqual(word, "sent")

    # ----------------------------------------------------------------- #
    # #478 — a watchdog-INITIATED auto-re-arm (origin="dark-rearm") must
    # honour the recent-human gate at the keystroke point, UNLIKE the user's
    # own /autopilot callback (whose origin IS the user, so it stays exempt).
    # ----------------------------------------------------------------- #

    def test_dark_rearm_origin_skips_on_recent_human(self):
        # A dark-watch auto-re-arm is watchdog-INITIATED, so at DELIVERY time
        # it MUST refuse while a human is active — never type /goal into a
        # pane a human just touched (they may have deliberately stopped the
        # loop without a `/goal clear`). RED: deliver_goal has no origin gate.
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(True, "presence marker fresh")):
            word, tmux, _ = self._go(GOAL_IDLE_CAP, origin="dark-rearm")
        self.assertEqual(word, "skip:recent-human")
        self.assertEqual(tmux.sent, [])          # never typed

    def test_dark_rearm_origin_sends_when_no_recent_human(self):
        # With the gate returning "no recent human", a dark-rearm delivers
        # normally through the verified keystroke path.
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "")):
            word, tmux, _ = self._go(GOAL_IDLE_CAP, origin="dark-rearm")
        self.assertEqual(word, "sent")
        self.assertIn(self.TEXT, tmux.typed_texts())

    def test_normal_origin_stays_exempt_from_recent_human(self):
        # The self-callback (user typed /autopilot) origin stays EXEMPT: its
        # origin IS the user. A recent human must NOT block a normal arm —
        # applying the gate here would be the structurally-always-refuses bug
        # this module's header warns about. Teeth against a mutant that gates
        # ALL origins.
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(True, "would block if applied")):
            word, tmux, _ = self._go(GOAL_IDLE_CAP)      # origin defaults to None
        self.assertEqual(word, "sent")
        self.assertIn(self.TEXT, tmux.typed_texts())

    def test_dark_rearm_skips_when_transcript_vanished(self):
        # #478 review MINOR — if the transcript vanished between pane
        # resolution and deliver_goal's own re-query (delete/archive race),
        # a dark-rearm must refuse on unprovable state (no recent-human gate
        # possible) rather than type blind. Forced by resolving a pane but
        # making find_active_transcript return None.
        from watchdog import compact as _compact
        proj = self._dir()
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        with m.patch.object(_compact, "_find_pane_for_session", return_value="%9"), \
             m.patch.object(wd, "find_active_transcript", return_value=None):
            word = goal.deliver_goal(self.SID, self.CWD, self.TEXT, "full",
                                     run=tmux, projects_dir=proj,
                                     origin="dark-rearm", sleep_fn=lambda s: None)
        self.assertEqual(word, "skip:no-transcript")
        self.assertEqual(tmux.sent, [])                  # never typed

    def test_normal_origin_not_gated_on_a_missing_transcript(self):
        # The mirror: the no-transcript refusal is dark-rearm-ONLY. A normal
        # (non-dark-rearm) origin passes THROUGH to the keystroke machinery
        # even when the transcript is absent — it must never hit
        # skip:no-transcript. (The contrived no-transcript fake makes the
        # verified-send readback fail, so assert only the property under
        # test: it is NOT the dark-rearm refusal.)
        from watchdog import compact as _compact
        proj = self._dir()
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        with m.patch.object(_compact, "_find_pane_for_session", return_value="%9"), \
             m.patch.object(wd, "find_active_transcript", return_value=None):
            word = goal.deliver_goal(self.SID, self.CWD, self.TEXT, "full",
                                     run=tmux, projects_dir=proj,
                                     sleep_fn=lambda s: None)
        self.assertNotEqual(word, "skip:no-transcript",
                            "the no-transcript refusal is dark-rearm-only")

    def test_bootstrap_turn_ending_needs_you_still_arms(self):
        # #403-review CRITICAL C1: the /autopilot bootstrap turn that
        # RECORDS this very request typically ends its OWN turn on a ❓/⏳
        # status marker (measured against the real corpus: 98% of real
        # bootstrap turns do). The session then sits idle at that exact
        # marker forever, so a gate refusing to arm while the transcript's
        # last marker is ❓/⏳ would refuse nearly every real arm,
        # PERMANENTLY -- goal_sweep re-evaluates the SAME stale marker on
        # every later sweep too, never just once. Arming here is safe: the
        # downstream boundary/dialog/copy-mode checks already cover the
        # only genuinely unsafe pane states, and the template's own
        # condition (A) already refuses to let the loop proceed past an
        # unanswered ❓ regardless of whether it's armed.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 marker_text="❓ NEEDS YOU: some question")
        word, tmux, _ = self._go(GOAL_IDLE_CAP, proj=proj)
        self.assertEqual(word, "sent")
        self.assertIn(self.TEXT, tmux.typed_texts())

    def test_bootstrap_turn_ending_working_still_arms(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 marker_text="⏳ WORKING: still going")
        word, tmux, _ = self._go(GOAL_IDLE_CAP, proj=proj)
        self.assertEqual(word, "sent")

    def test_kill_switch_disables_delivery(self):
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            word, tmux, _ = self._go(GOAL_IDLE_CAP)
        self.assertEqual(word, "skip:disabled")
        self.assertEqual(tmux.sent, [])

    def test_send_is_logged(self):
        self._go(GOAL_IDLE_CAP)
        log = self.syncp.read_text(encoding="utf-8")
        self.assertIn("SEND", log)
        self.assertIn(self.SID, log)

    def test_skip_is_logged(self):
        self._go(GOAL_BUSY_CAP)
        log = self.syncp.read_text(encoding="utf-8")
        self.assertIn("SKIP undeterminable", log)


# --------------------------------------------------------------------------- #
# 4. goal_sweep — job 9's new body: the periodic re-evaluation loop.
# --------------------------------------------------------------------------- #

class TestScanGoalMarkersSimplified(unittest.TestCase):
    CWD = "/home/newlevel/devel/scanmarkers"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_marker_dict_has_no_arm_after_key(self):
        proj = self._dir()
        sid = "sess-scan-1"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tpath = proj / _encode(self.CWD) / (sid + ".jsonl")
        _off, mark = wd.scan_goal_markers(tpath, off=0)
        self.assertIsNotNone(mark)
        self.assertNotIn("arm_after", mark)

    def test_does_not_crash_on_an_arm_question_shaped_line(self):
        # The OLD design's `_entry_asks_to_arm`/`_GOAL_ASK_PROBE` used to
        # classify a line CONTAINING the literal bytes "/goal" as a
        # possible arm-question -- confirm the simplified scan reads
        # straight past such a line with no error, finding only the real
        # marker.
        proj = self._dir()
        sid = "sess-scan-2"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tpath = proj / _encode(self.CWD) / (sid + ".jsonl")
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {
                "content": "Should I paste the /goal line now?"}}) + "\n")
        _off, mark = wd.scan_goal_markers(tpath, off=0)
        self.assertIsNotNone(mark)
        self.assertEqual(mark.get("state"), "set")


# --------------------------------------------------------------------------- #
# X. CLI wiring — `airuleset.py goal-arm --self` (#403-review MAJOR M3): the
#    model's ONE proven origin had zero test coverage anywhere. Mirrors
#    test_compact.py's own `TestCompactRequestCli` shape.
# --------------------------------------------------------------------------- #

def _garm_args(**kw):
    kw.setdefault("self", False)
    kw.setdefault("template", "")
    return types.SimpleNamespace(**kw)


class TestGoalArmCli(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def test_no_self_flag_exits_nonzero(self):
        with m.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as cm:
                airuleset.cmd_goal_arm(_garm_args())
        self.assertNotEqual(cm.exception.code, 0)

    def test_unresolvable_pane_exits_nonzero(self):
        with m.patch.object(goal._compact, "resolve_self_pane",
                            return_value=("", "", "")):
            with m.patch("sys.stderr"):
                with self.assertRaises(SystemExit) as cm:
                    airuleset.cmd_goal_arm(_garm_args(self=True))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(goal.load_goal_requests(self.reqp), {})

    def test_unresolvable_template_exits_nonzero(self):
        with m.patch.object(goal._compact, "resolve_self_pane",
                            return_value=("%3", "/somewhere", "sess-a")):
            with m.patch.object(goal, "goal_template_for_authority",
                                return_value=""):
                with m.patch("sys.stderr"):
                    with self.assertRaises(SystemExit) as cm:
                        airuleset.cmd_goal_arm(_garm_args(self=True))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(goal.load_goal_requests(self.reqp), {})

    def test_self_records_under_resolved_authority_and_prints_disposition(self):
        # Hermetic: never read the box's REAL installed SKILL.md (the same
        # #403-review round-2 MINOR-2 gap as the e2e test below — this test
        # only cares about the recorded entry + printed disposition).
        with m.patch.object(goal._compact, "resolve_self_pane",
                            return_value=("%3", "/somewhere", "sess-b")):
            with m.patch("airuleset.resolve_authority", return_value="full"), \
                    m.patch.object(goal, "goal_template_for_authority",
                                   return_value="/goal test-template"):
                with m.patch.object(goal, "deliver_goal",
                                    return_value="skip:no-pane"):
                    buf = []
                    with m.patch("sys.stdout") as out:
                        out.write = lambda s: buf.append(s)
                        airuleset.cmd_goal_arm(_garm_args(self=True))
        self.assertEqual("".join(buf), "skip:no-pane")
        entry = goal.load_goal_requests(self.reqp).get("sess-b")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["authority"], "full")
        self.assertEqual(entry["origin"], "self-callback")

    def test_explicit_template_overrides_resolve_authority(self):
        with m.patch.object(goal._compact, "resolve_self_pane",
                            return_value=("%3", "/somewhere", "sess-c")):
            with m.patch("airuleset.resolve_authority",
                        side_effect=AssertionError("must not be called "
                                                    "when --template is given")), \
                    m.patch.object(goal, "goal_template_for_authority",
                                   return_value="/goal test-template"):
                with m.patch.object(goal, "deliver_goal",
                                    return_value="skip:no-pane"):
                    with m.patch("sys.stdout"):
                        airuleset.cmd_goal_arm(
                            _garm_args(self=True, template="fork-no-merge"))
        entry = goal.load_goal_requests(self.reqp).get("sess-c")
        self.assertEqual(entry["authority"], "fork-no-merge")

    def test_self_on_a_genuinely_idle_pane_actually_sends_end_to_end(self):
        # Not a mocked `deliver_goal` here -- a real call, through the
        # real CLI entry point, against a real (fake-tmux) idle pane, all
        # the way down. `goal_templates_path` is pinned to an isolated
        # fixture (#403-review round-2 MINOR 2: this was the ONLY test in
        # the file reading the box's REAL installed skills/autopilot/
        # SKILL.md via the default resolver -- non-hermetic, passes only
        # on a provisioned box, the same class of gap #368-review MAJOR-3
        # already fixed one day earlier in this repo).
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        cwd = "/home/newlevel/devel/goal-cli-e2e"
        sid = "sess-cli-e2e"
        _write_marker_transcript(proj.name, cwd, sid)
        skill_md = Path(proj.name) / "SKILL.md"
        skill_md.write_text(_skill_md(("full", "/goal STOP CONDITIONS "
                                       "isolated test fixture")),
                            encoding="utf-8")
        tmux = DeliverGoalFakeTmux([("%9", "claude", cwd, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(goal._compact, "resolve_self_pane",
                            return_value=("%9", cwd, sid)):
            with m.patch.object(wd, "_default_run", tmux):
                with m.patch.object(wd, "PROJECTS_DIR", proj.name):
                    with m.patch.object(wd, "goal_templates_path",
                                        return_value=str(skill_md)):
                        with m.patch("airuleset.resolve_authority",
                                    return_value="full"):
                            buf = []
                            with m.patch("sys.stdout") as out:
                                out.write = lambda s: buf.append(s)
                                airuleset.cmd_goal_arm(_garm_args(self=True))
        self.assertEqual("".join(buf), "sent")
        self.assertIn("/goal STOP CONDITIONS isolated test fixture",
                      tmux.typed_texts())
        # Cleared on the terminal word -- nothing left pending.
        self.assertEqual(goal.load_goal_requests(self.reqp), {})


if __name__ == "__main__":
    unittest.main()
