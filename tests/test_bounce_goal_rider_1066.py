"""#1066 lane B — the `/goal` loop proves `bounces-unhandled == 0`, the INFRA
window proves its OWN slice, and a fresh gk BOUNCE verdict wakes the armed
reduced-authority stream within minutes.

Lane A (merged, v0.1.396) produces `entry["bounce_unhandled"]` (a per-cwd
tickets-status cache field `[{"number": N, "verdict_ts": <epoch>}, ...]`) and
`slice-quals --bounces --unhandled`. This lane CONSUMES them in THREE places:

  (item 1) goal_registry.py — the reduced (B) `proof` clause names
     `slice-quals --bounces --unhandled` and states a `bounce K` BLOCKS 🏁
     until it prints nothing; the FULL profile does NOT (a bare sub-dev bounce
     is not gk's obligation).
  (item 2) render_goal_line — the FULL gk-infra window's proof is role-scoped
     (`core-quals --role infra --count`). The substitution is FULL-only
     (documented in the Anchors-confirmed comment): applying it to the
     lock-only reduced+infra defensive variants would breach the design's
     explicit ≥ 20-headroom invariant, and finding (a) is about the
     full-authority gk-infra window (core-quals). Non-infra variants are
     unaffected by the item-2 substitution.
  (item 3) watchdog/bounce_verdict_recheck.py — a new goal-lane rider (the
     faithful #733 queue-arrival sibling on goal_lane_sweep's EXISTING armed
     pane loop, ZERO new pane walk): per armed REDUCED-authority pane it reads
     `bounce_unhandled` from the cache and, on a SET DELTA over
     (number, verdict_ts) pairs, delivers ONE verified `stuck-check: BOUNCE #N
     (gk HH:MM) — ACK + lane within 1 h` nudge under nudge_gate's 60-min floor.
  (item 4) cross_stream.bounce_backstop — job 8's dedup key becomes a
     `N@<verdict_ts>` token set, so a FRESH verdict on a seen ticket re-nudges
     inside the 6 h window; an unchanged verdict does not.

RED against the pre-implementation tree: `from watchdog import
bounce_verdict_recheck` ImportErrors (module absent), and the registry / job-8
edits are not made yet. GREEN once the module + edits + wiring land.
"""

import os
import subprocess
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import goal_registry as gr  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import bounce_verdict_recheck as bv  # noqa: E402  (RED: absent)
from watchdog import queue_arrival_recheck as qa  # noqa: E402
from watchdog import nudge_gate  # noqa: E402
from watchdog import tmux_io  # noqa: E402
from watchdog import cross_stream  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

REPO = Path(__file__).resolve().parent.parent
NOW = 1_000_000
DAY = 24 * 3600
# two distinct gk verdict timestamps for the same ticket (a re-bounce)
TS1 = 1_790_000_000
TS2 = 1_790_050_000


# --------------------------------------------------------------------------- #
# (a) — the reduced (B) proof names the unhandled flag; the full profile does not
# --------------------------------------------------------------------------- #

class TestBounceProofClauseReduced(unittest.TestCase):
    def test_both_reduced_proofs_name_the_unhandled_flag(self):
        # #1128 (owner ruling 2026-09-23): the reduced (B) proof — and its
        # "`bounce K` BLOCKS 🏁" wording — is gone (a stream loop has no done-
        # state). The lane-A flag survives as the IDLE gate: the loop idles on
        # its stream-wait only once the flag prints nothing.
        for prof in ("branch-merge", "fork-no-merge"):
            for mode in ("parallel", "sequential"):
                line = gr.render_goal_line(prof, mode, None)
                self.assertIn("slice-quals --bounces --unhandled", line,
                              "%s/%s idle gate must name the lane-A flag" % (prof, mode))
                # STATEMENT lock: the flag printing nothing == no unhandled bounce
                self.assertIn("prints nothing", line, prof)
                self.assertLess(line.index("--bounces --unhandled"),
                                line.index("stream-wait"), prof)

    def test_full_proof_has_no_bounce_clause(self):
        for mode in ("parallel", "sequential"):
            line = gr.render_goal_line("full", mode, None)
            self.assertNotIn("slice-quals --bounces --unhandled", line)
            self.assertNotIn("bounce K", line)

    def test_the_old_verbose_parenthetical_is_gone_from_reduced(self):
        # the pre-#1066 parenthetical is REPLACED, not appended to (net budget)
        for prof in ("branch-merge", "fork-no-merge"):
            line = gr.render_goal_line(prof, "sequential", None)
            self.assertNotIn("gatekeeper-owned/user-parked/ops-wait", line)


# --------------------------------------------------------------------------- #
# (b) — the FULL infra proof is role-scoped; the item-2 substitution is isolated
# --------------------------------------------------------------------------- #

class TestInfraRoleScopedProof(unittest.TestCase):
    def test_full_infra_proof_names_role_infra(self):
        line = gr.render_goal_line("full", "sequential", "infra")
        self.assertIn("core-quals --role infra --count", line)

    def test_full_non_infra_proof_stays_plain_core_quals(self):
        for mode in ("parallel", "sequential"):
            line = gr.render_goal_line("full", mode, None)
            self.assertIn("core-quals --count", line)
            self.assertNotIn("--role infra --count", line)

    def test_the_item2_substitution_never_leaks_to_a_non_infra_variant(self):
        for a, mode, role in gr.variant_specs():
            if role == "infra":
                continue
            line = gr.render_goal_line(a, mode, role)
            self.assertNotIn("--role infra --count", line,
                             "%s/%s/%s must not carry the infra substitution"
                             % (a, mode, role))
        # the review variant too (not in variant_specs)
        self.assertNotIn("--role infra --count",
                         gr.render_goal_line("full", "parallel", "review"))

    def test_reduced_infra_keeps_plain_slice_quals_count(self):
        # documented HOW-decision (Anchors-confirmed): item 2 is FULL-only, so
        # the lock-only reduced+infra defensive variants keep the plain command
        # and hold the ≥ 20-headroom invariant.
        for prof in ("branch-merge", "fork-no-merge"):
            line = gr.render_goal_line(prof, "sequential", "infra")
            self.assertIn("slice-quals --count", line)
            self.assertNotIn("--role infra --count", line)


# --------------------------------------------------------------------------- #
# (c) — every variant renders < 4000 with >= 20 headroom; SKILL.md lines >= 150
# --------------------------------------------------------------------------- #

class TestVariantHeadroom(unittest.TestCase):
    CAP = gr.GOAL_ARM_CHAR_CAP
    MIN_HEADROOM = 20
    MIN_SKILL_HEADROOM = 150

    def test_every_variant_render_under_cap_with_margin(self):
        short = []
        for a, mode, role in gr.variant_specs():
            line = gr.render_goal_line(a, mode, role)
            hr = self.CAP - len(line)   # len() == code points
            if hr < self.MIN_HEADROOM:
                short.append(("%s/%s/%s" % (a, mode, role), hr))
        # the review variant (not in variant_specs) is checked too
        rl = gr.render_goal_line("full", "parallel", "review")
        if self.CAP - len(rl) < self.MIN_HEADROOM:
            short.append(("full/parallel/review", self.CAP - len(rl)))
        self.assertEqual(short, [], "variants below >=%d headroom: %r"
                         % (self.MIN_HEADROOM, short))

    def test_variant_check_stays_clean(self):
        self.assertEqual(gr.variant_check(), [])

    def test_the_three_skill_md_lines_keep_150_headroom(self):
        for prof in gr.PROFILES:
            hr = self.CAP - len(gr.render(prof))   # render() == parallel/no-role
            self.assertGreaterEqual(
                hr, self.MIN_SKILL_HEADROOM,
                "%s SKILL.md /goal line headroom %d < %d"
                % (prof, hr, self.MIN_SKILL_HEADROOM))

    def test_variants_item_1_and_2_never_touch_are_byte_identical_to_main(self):
        # the variants that carry NEITHER the reduced proof (item 1) NOR the
        # infra substitution (item 2) must render byte-for-byte as main did.
        main_gr = _load_main_goal_registry()
        untouched = [("full", "parallel", None), ("full", "sequential", None),
                     ("full", "sequential", "quality"),
                     ("full", "parallel", "review")]
        for a, mode, role in untouched:
            self.assertEqual(
                gr.render_goal_line(a, mode, role),
                main_gr.render_goal_line(a, mode, role),
                "%s/%s/%s drifted from main" % (a, mode, role))


def _load_main_goal_registry():
    """Import `git show main:goal_registry.py` as a throwaway module (the #1074
    snapshot pattern) so the byte-identical lock reads the SHIPPED main render,
    not a hardcoded copy."""
    import importlib.util
    src = subprocess.check_output(
        ["git", "-C", str(REPO), "show", "main:goal_registry.py"])
    spec = importlib.util.spec_from_loader("goal_registry_main", loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(src, "goal_registry_main.py", "exec"), mod.__dict__)
    return mod


# --------------------------------------------------------------------------- #
# (d.pure) — the (number, verdict_ts) token set-delta, reusing qa._queue_decision
# --------------------------------------------------------------------------- #

class TestBounceTokens(unittest.TestCase):
    def test_token_is_deterministic_and_pair_unique(self):
        self.assertEqual(bv._encode_token(6474, TS1), bv._encode_token(6474, TS1))
        self.assertNotEqual(bv._encode_token(6474, TS1), bv._encode_token(6474, TS2))
        self.assertNotEqual(bv._encode_token(6474, TS1), bv._encode_token(6413, TS1))

    def test_none_field_is_undetermined(self):
        cur, tmap = bv._tokens_and_map(None)
        self.assertIsNone(cur)

    def test_non_list_field_is_undetermined(self):
        cur, tmap = bv._tokens_and_map({"boom": 1})
        self.assertIsNone(cur)

    def test_valid_entries_encode_to_tokens_with_a_reverse_map(self):
        cur, tmap = bv._tokens_and_map(
            [{"number": 6474, "verdict_ts": TS1},
             {"number": 6413, "verdict_ts": TS2}])
        self.assertEqual(len(cur), 2)
        self.assertEqual(tmap[bv._encode_token(6474, TS1)], (6474, TS1))

    def test_malformed_entry_is_skipped_not_fatal(self):
        cur, tmap = bv._tokens_and_map(
            [{"number": 6474, "verdict_ts": TS1},
             {"number": "x", "verdict_ts": TS1},
             {"number": 6413}])
        # only the one valid entry survives
        self.assertEqual(list(tmap.values()), [(6474, TS1)])

    def test_reuses_qa_queue_decision_verbatim(self):
        # the design: reuse _queue_decision by import, never copy.
        self.assertIs(bv._queue_decision, qa._queue_decision)
        self.assertIs(bv._advanced_base, qa._advanced_base)

    def test_seed_then_new_pair_then_rebounce(self):
        cur1, _ = bv._tokens_and_map([{"number": 6474, "verdict_ts": TS1}])
        act, rec, reason, arr = bv._queue_decision({}, cur1, NOW)
        self.assertEqual(act, "seed")
        # a genuinely NEW pair over the seeded baseline nudges
        cur2, _ = bv._tokens_and_map(
            [{"number": 6474, "verdict_ts": TS1},
             {"number": 6413, "verdict_ts": TS2}])
        act, rec2, reason, arr = bv._queue_decision(rec, cur2, NOW)
        self.assertEqual(act, "nudge")
        self.assertEqual(arr, [bv._encode_token(6413, TS2)])
        # a RE-BOUNCE (same number, newer verdict_ts) is a NEW token -> nudge
        cur3, _ = bv._tokens_and_map([{"number": 6474, "verdict_ts": TS2}])
        act, _, _, arr = bv._queue_decision({"base": [bv._encode_token(6474, TS1)],
                                             "first_seen": NOW - DAY}, cur3, NOW)
        self.assertEqual(act, "nudge")
        self.assertEqual(arr, [bv._encode_token(6474, TS2)])


# --------------------------------------------------------------------------- #
# (d) — the rider orchestrator
# --------------------------------------------------------------------------- #

class _RiderBase(unittest.TestCase):
    CWD = "/home/montalu1/devel/odoo/odoo-slovnormal"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-1066-b")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, brecs, fetch, tmux, *, dry_run=False, handled=None,
             state=None, authority="fork-no-merge", captured=None):
        with m.patch("airuleset.resolve_authority", return_value=authority):
            return bv.goal_bounce_verdict_recheck(
                NOW, tmux, brecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                dry_run, handled, bounce_unhandled_fetch=fetch,
                state=state if state is not None else {},
                sleep_fn=lambda *a, **k: None, captured=captured)


class TestRiderOrchestrator(_RiderBase):
    def test_full_authority_pane_is_skipped(self):
        called = []
        brecs = {}
        logs = self._run(brecs, lambda cwd: called.append(cwd) or [],
                         self._tmux(), authority="full")
        self.assertTrue(any("skip:full-authority" in ln for ln in logs))
        self.assertEqual(called, [])
        self.assertEqual(brecs, {})

    def test_authority_unresolved_skips(self):
        brecs = {}
        with m.patch("airuleset.resolve_authority",
                     side_effect=RuntimeError("boom")):
            logs = bv.goal_bounce_verdict_recheck(
                NOW, self._tmux(), brecs, self.sid, self.CWD, "%9", self.tpath,
                "sess:0", False, set(),
                bounce_unhandled_fetch=lambda cwd: [{"number": 1, "verdict_ts": TS1}],
                state={}, sleep_fn=lambda *a, **k: None)
        self.assertTrue(any("skip:authority-unresolved" in ln for ln in logs))

    def test_unwired_fetch_none_is_skipped(self):
        brecs = {}
        logs = self._run(brecs, None, self._tmux())
        self.assertTrue(any("unwired" in ln or "skip" in ln for ln in logs))

    def test_missing_cache_is_undetermined_no_mutation(self):
        brecs = {self.sid: {"base": [1]}}
        logs = self._run(brecs, lambda cwd: None, self._tmux())
        self.assertTrue(any("skip:undetermined" in ln for ln in logs))
        self.assertEqual(brecs[self.sid], {"base": [1]})   # baseline untouched

    def test_non_list_field_no_baseline_move(self):
        brecs = {self.sid: {"base": [1]}}
        logs = self._run(brecs, lambda cwd: {"x": 1}, self._tmux())
        self.assertTrue(any("skip:undetermined" in ln for ln in logs))
        self.assertEqual(brecs[self.sid], {"base": [1]})

    def test_first_observation_seeds_no_keystroke(self):
        brecs = {}
        tmux = self._tmux()
        logs = self._run(brecs, lambda cwd: [{"number": 6474, "verdict_ts": TS1}],
                         tmux)
        self.assertTrue(any("seed" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(brecs[self.sid]["base"], [bv._encode_token(6474, TS1)])

    def test_new_pair_types_a_named_nudge_and_advances_base(self):
        seeded = bv._encode_token(6474, TS1)
        brecs = {self.sid: {"base": [seeded], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        handled = set()
        self._run(
            brecs,
            lambda cwd: [{"number": 6474, "verdict_ts": TS1},
                         {"number": 6413, "verdict_ts": TS2}],
            tmux, handled=handled, state={})
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)
        self.assertIn("BOUNCE #6413", typed)
        self.assertIn("ACK + lane within 1 h", typed)
        # the gk HH:MM is rendered from the verdict_ts
        self.assertRegex(typed, r"gk \d\d:\d\d")
        self.assertIn(bv._encode_token(6413, TS2), brecs[self.sid]["base"])
        self.assertIn(self.sid, handled)

    def test_rebounce_same_number_newer_verdict_nudges(self):
        old = bv._encode_token(6474, TS1)
        brecs = {self.sid: {"base": [old], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        self._run(brecs, lambda cwd: [{"number": 6474, "verdict_ts": TS2}],
                  tmux, handled=set(), state={})
        typed = "".join(tmux.typed_texts())
        self.assertIn("BOUNCE #6474", typed)
        # the stale (6474, TS1) token is dropped; base is the fresh token
        self.assertEqual(brecs[self.sid]["base"], [bv._encode_token(6474, TS2)])

    def test_inside_floor_holds_and_accumulates(self):
        seeded = bv._encode_token(6474, TS1)
        # a recent bounce-verdict send this hour -> gate_ok False
        state = {}
        nudge_gate.mark_sent(state, self.sid, "bounce-verdict", NOW - 600)
        brecs = {self.sid: {"base": [seeded], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        logs = self._run(
            brecs,
            lambda cwd: [{"number": 6474, "verdict_ts": TS1},
                         {"number": 6413, "verdict_ts": TS2}],
            tmux, handled=set(), state=state)
        self.assertTrue(any("hold:floor" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        # base kept OLD so the held member ACCUMULATES into the next post-floor nudge
        self.assertEqual(brecs[self.sid]["base"], [seeded])

    def test_recent_sibling_kind_holds_via_total_cap(self):
        # review A 🔵1 — the SECONDARY double-keystroke guard: even when a
        # same-sweep batch of a SIBLING kind stamped only the cross-kind floor
        # (mark_batch_sent, NOT `handled`), the rider's own gate_ok sees the 3h
        # total cap and holds — so at most ONE priority keystroke reaches the
        # pane per window regardless of which guard applies.
        seeded = bv._encode_token(6474, TS1)
        state = {}
        nudge_gate.mark_sent(state, self.sid, "queue-arrival", NOW)   # a sibling kind
        brecs = {self.sid: {"base": [seeded], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        logs = self._run(
            brecs,
            lambda cwd: [{"number": 6474, "verdict_ts": TS1},
                         {"number": 6413, "verdict_ts": TS2}],
            tmux, handled=set(), state=state)
        self.assertTrue(any("hold:total-cap" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(brecs[self.sid]["base"], [seeded])   # base kept OLD

    def test_already_handled_defers_no_keystroke(self):
        seeded = bv._encode_token(6474, TS1)
        brecs = {self.sid: {"base": [seeded], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        logs = self._run(
            brecs,
            lambda cwd: [{"number": 6474, "verdict_ts": TS1},
                         {"number": 6413, "verdict_ts": TS2}],
            tmux, handled={self.sid}, state={})
        self.assertTrue(any("already-handled" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])

    def test_dry_run_would_nudge_no_mutation(self):
        seeded = bv._encode_token(6474, TS1)
        brecs = {self.sid: {"base": [seeded], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        logs = self._run(
            brecs,
            lambda cwd: [{"number": 6474, "verdict_ts": TS1},
                         {"number": 6413, "verdict_ts": TS2}],
            tmux, dry_run=True)
        self.assertTrue(any("WOULD-NUDGE" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(brecs[self.sid]["base"], [seeded])   # unadvanced

    def test_swallowed_submit_does_not_advance_base(self):
        seeded = bv._encode_token(6474, TS1)
        brecs = {self.sid: {"base": [seeded], "first_seen": NOW - DAY}}
        tmux = self._tmux(enters_swallowed=5)
        handled = set()
        logs = self._run(
            brecs,
            lambda cwd: [{"number": 6474, "verdict_ts": TS1},
                         {"number": 6413, "verdict_ts": TS2}],
            tmux, handled=handled, state={})
        self.assertTrue(any("submit-unverified" in ln for ln in logs))
        self.assertEqual(brecs[self.sid]["base"], [seeded])   # retry next sweep
        self.assertNotIn(self.sid, handled)


# --------------------------------------------------------------------------- #
# rider orphan reaper
# --------------------------------------------------------------------------- #

class TestRiderPruneOrphans(unittest.TestCase):
    def test_aged_not_visited_reaped(self):
        brecs = {"gone": {"base": [1], "lts": NOW - 2 * DAY}}
        bv._prune_bounce_verdict_orphans(brecs, set(), NOW)
        self.assertEqual(brecs, {})

    def test_visited_never_reaped(self):
        brecs = {"live": {"base": [1], "lts": NOW - 2 * DAY}}
        bv._prune_bounce_verdict_orphans(brecs, {"live"}, NOW)
        self.assertIn("live", brecs)

    def test_non_dict_never_raises(self):
        bv._prune_bounce_verdict_orphans("boom", set(), NOW)


# --------------------------------------------------------------------------- #
# nudge-kind registration
# --------------------------------------------------------------------------- #

class TestKindRegistration(unittest.TestCase):
    def test_bounce_verdict_is_a_gated_work_driving_category(self):
        self.assertIn("bounce-verdict", nudge_gate.GATED_CATEGORIES)
        self.assertIn("bounce-verdict", nudge_gate.WORK_DRIVING_CATEGORIES)

    def test_bounce_verdict_floor_is_the_60_min_default(self):
        self.assertEqual(nudge_gate._category_floor("bounce-verdict"),
                         nudge_gate._min_interval())

    def test_bounce_verdict_is_a_stageable_machine_kind(self):
        self.assertIn("bounce-verdict", tmux_io.MACHINE_NUDGE_KINDS)
        self.assertNotIn("bounce-verdict", tmux_io.RECOVERY_NUDGE_KINDS)

    def test_the_nudges_cli_kind_list_derives_it(self):
        out = subprocess.run(
            [sys.executable, str(REPO / "airuleset.py"), "nudges", "on"],
            capture_output=True, text=True)
        self.assertIn("bounce-verdict", out.stdout)


# --------------------------------------------------------------------------- #
# (e) — job 8 dedup keyed on the verdict token
# --------------------------------------------------------------------------- #

class TestJob8VerdictTokens(unittest.TestCase):
    """cross_stream.bounce_backstop: seen[name]['tickets'] becomes a
    `N@<verdict_ts>` token set (verdict from the per-cwd bounce_unhandled cache,
    quota-neutral), so a fresh verdict on a seen ticket is a CHANGED set that
    re-nudges inside the 6 h window; an unchanged verdict does not."""

    def test_token_helper_builds_N_at_verdict(self):
        toks = cross_stream._bounce_seen_tokens(
            [6474, 6413], {6474: TS1})
        # 6474 has a verdict -> N@ts; 6413 has none -> bare N
        self.assertIn("6474@%d" % TS1, toks)
        self.assertIn("6413", toks)

    def test_unchanged_verdict_is_the_same_set(self):
        a = cross_stream._bounce_seen_tokens([6474], {6474: TS1})
        b = cross_stream._bounce_seen_tokens([6474], {6474: TS1})
        self.assertEqual(a, b)

    def test_newer_verdict_is_a_changed_set(self):
        a = cross_stream._bounce_seen_tokens([6474], {6474: TS1})
        b = cross_stream._bounce_seen_tokens([6474], {6474: TS2})
        self.assertNotEqual(a, b)


class TestJob8MaterialChange(unittest.TestCase):
    """review B 🟡 — the dedup MATERIAL-change semantics: only a ticket
    add/remove or a strictly-NEWER known verdict re-nudges; a bare-N<->N@ts
    cache-visibility transition and the int->token state migration do NOT."""

    def test_added_or_removed_ticket_is_material(self):
        self.assertTrue(cross_stream._bounce_material_change(
            ["6474"], ["6474", "6413"]))
        self.assertTrue(cross_stream._bounce_material_change(
            ["6474", "6413"], ["6474"]))

    def test_newer_verdict_on_a_seen_ticket_is_material(self):
        self.assertTrue(cross_stream._bounce_material_change(
            ["6474@%d" % TS1], ["6474@%d" % TS2]))

    def test_older_or_same_verdict_is_not_material(self):
        self.assertFalse(cross_stream._bounce_material_change(
            ["6474@%d" % TS2], ["6474@%d" % TS1]))   # older -> not material
        self.assertFalse(cross_stream._bounce_material_change(
            ["6474@%d" % TS1], ["6474@%d" % TS1]))   # same

    def test_cache_visibility_transition_is_not_material(self):
        # bare N -> N@ts (cache empty->populated): job 8 already nudged on
        # presence; the fast rider owns the verdict -> NOT a re-nudge.
        self.assertFalse(cross_stream._bounce_material_change(
            ["6474"], ["6474@%d" % TS1]))
        self.assertFalse(cross_stream._bounce_material_change(
            ["6474@%d" % TS1], ["6474"]))   # verdict cache lost -> not material

    def test_int_to_token_migration_is_not_material(self):
        # a pre-#1066 int-list state vs the new string-token list must NOT
        # spuriously re-nudge at the deploy boundary.
        self.assertFalse(cross_stream._bounce_material_change([6474], ["6474"]))
        self.assertFalse(cross_stream._bounce_material_change(
            [6474, 6413], ["6413", "6474"]))

    def test_none_and_malformed_never_raise(self):
        self.assertFalse(cross_stream._bounce_material_change(None, None))
        self.assertTrue(cross_stream._bounce_material_change(None, ["6474"]))
        # a malformed token is skipped, not fatal
        self.assertFalse(cross_stream._bounce_material_change(["x@y"], []))


# --------------------------------------------------------------------------- #
# integration — goal_lane_sweep accepts + drives the rider
# --------------------------------------------------------------------------- #

class TestLaneSweepWiring(unittest.TestCase):
    def test_goal_lane_sweep_accepts_bounce_unhandled_fetch(self):
        import inspect
        sig = inspect.signature(goal.goal_lane_sweep)
        self.assertIn("bounce_unhandled_fetch", sig.parameters)

    def test_state_namespace_created_when_wired(self):
        # a wired fetch creates state['bounce_verdict']; unwired leaves it absent
        with TemporaryDirectory() as proj:
            state = {}
            goal.goal_lane_sweep(
                NOW, run=DeliverGoalFakeTmux([], GOAL_ARMED_CAP),
                dry_run=True, projects_dir=proj, state=state,
                backlog_fetch=lambda cwd: 0,
                bounce_unhandled_fetch=lambda cwd: [])
            self.assertIn("bounce_verdict", state)


# --------------------------------------------------------------------------- #
# (f) — goal-inventory --check clean after --write
# --------------------------------------------------------------------------- #

class TestGoalInventoryClean(unittest.TestCase):
    def test_goal_inventory_check_is_clean(self):
        out = subprocess.run(
            [sys.executable, str(REPO / "airuleset.py"), "goal-inventory", "--check"],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0,
                         "goal-inventory --check not clean:\n%s\n%s"
                         % (out.stdout, out.stderr))


if __name__ == "__main__":
    unittest.main()
