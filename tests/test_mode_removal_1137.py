"""#1137 (owner 25.10.2026) — the concurrency-MODE machinery is removed.

The owner wants neither a "sequential" nor a "parallel" mode: Claude Code works
by its own judgement (subagents or not, how many, worktrees or not, ticket
order). airuleset stops steering that. Removed: the sequential/parallel
resolver, the `/goal` mode clauses, the dispatch lane-count gate, the lane
overlap receipt gate, the lane-fill Stop gate, the lane-resource caps, the
lane-occupancy / lane-reconcile / queue-arrival refill nudges and the always-on
lane doctrine. Kept: window ROLES (review / infra / quality), every stop
condition, the account-block owner ping, and ONE short encouragement to bundle
related tickets in the `/goal` text.
"""

import hashlib
import inspect
import json
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_concurrency  # noqa: E402
import cli_fleet  # noqa: E402
import goal_registry  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal, stash  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    DeliverGoalFakeTmux,
    _encode,
    _write_marker_transcript,
)

BUNDLE = "one branch, one PR and one CI cycle"
MONTHLY = "You've hit your monthly spend limit · raise it at claude.ai/settings/usage"
WEEKLY = "You've hit your weekly limit · resets Aug 20, 1am (Europe/Bratislava)"
_OLD = json.loads((Path(__file__).resolve().parent / "fixtures"
                   / "pre_1137_mode_goals.json").read_text(encoding="utf-8"))


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


class TestRegistryHasNoModes(unittest.TestCase):
    def test_mode_symbols_are_gone(self):
        for name in ("MODES", "DEFAULT_MODE", "_SEQUENTIAL_SATURATION",
                     "_REVIEW_D_PARALLEL", "_REVIEW_D_SEQUENTIAL",
                     "skill_sequential_drift", "SATURATION_RECONCILES_COMPACT"):
            self.assertFalse(hasattr(goal_registry, name), name)

    def test_render_takes_no_mode(self):
        params = inspect.signature(goal_registry.render_goal_line).parameters
        self.assertNotIn("mode", params)

    def test_no_saturation_clause_ids(self):
        ids = {c.id for c in goal_registry.CLAUSES}
        self.assertFalse(ids & {"saturation-core", "saturation-delivery"}, ids)

    def test_every_variant_bundles_and_never_steers_lanes(self):
        specs = goal_registry.variant_specs()
        roles = {s[-1] for s in specs}
        self.assertTrue({None, "review", "infra", "quality"} <= roles, roles)
        for spec in specs:
            line = goal_registry.render_goal_line(*spec)
            for banned in ("ONE unit at a time", "CONTINUOUS REFILL",
                           "SEQUENTIAL", "lanes N/cap", "FREE lane",
                           "sloty", "lanes plním"):
                self.assertNotIn(banned, line, (spec, banned))
            self.assertEqual(line.count(BUNDLE), 1, spec)
            self.assertLessEqual(len(line), goal_registry.GOAL_ARM_CHAR_CAP)

    def test_lines_are_net_shorter(self):
        old = _OLD["full/sequential/None"]
        self.assertLess(len(goal_registry.render("full")), len(old))

    def test_inventory_check_is_clean(self):
        self.assertEqual(goal_registry.variant_check(), [])
        skill = _read("skills/autopilot/SKILL.md")
        self.assertEqual(goal_registry.drift(skill), [])


class TestJanitorKnowsLegacyGoals(unittest.TestCase):
    """A box may still hold a stranded pre-removal `/goal`: the janitor must keep
    recognising it as our own text during the transition."""

    def setUp(self):
        self._memo = stash._GOAL_TEMPLATE_VARIANTS
        stash._GOAL_TEMPLATE_VARIANTS = None
        self.addCleanup(setattr, stash, "_GOAL_TEMPLATE_VARIANTS", self._memo)

    def test_every_old_variant_is_still_listed(self):
        have = {hashlib.sha256(v.encode()).hexdigest()[:16]
                for v in goal_registry.all_goal_line_variants()}
        missing = {k for k, h in _OLD["sha256"].items() if h not in have}
        self.assertEqual(missing, set())

    def test_current_variants_are_listed(self):
        variants = goal_registry.all_goal_line_variants()
        for spec in goal_registry.variant_specs():
            self.assertIn(goal_registry.render_goal_line(*spec), variants)

    def test_old_sequential_box_is_own_leftover(self):
        old = _OLD["full/sequential/None"]
        cut = old.index("no refill;") + len("no refill;")
        rows, i = [], 0
        while i < cut:
            rows.append(("" if not rows else "  ") + old[i:i + 174])
            i += 174
        rows = rows[-3:]
        rows[0] = "❯ " + rows[0].lstrip()
        cap = "\n".join(["  transcript line"] * 20 + ["─" * 176] + rows
                        + ["─" * 176, "  ? for shortcuts"]) + "\n"
        self.assertTrue(stash._box_is_own_leftover(
            cap, None, stash.GOAL_ARM_LEFTOVER_MIN_SUBSTR, match_templates=True))


class TestRemovedFiles(unittest.TestCase):
    def test_files_are_gone(self):
        for rel in ("cli_dispatch_gate.py", "cli_lane_overlap.py",
                    "gates/lanefill.py", "watchdog/lane_resources.py",
                    ".claude/lane-resources.json",
                    "hooks/stop-check-lane-fill.sh"):
            self.assertFalse((REPO / rel).exists(), rel)

    def test_lane_fill_hook_not_registered(self):
        self.assertNotIn("stop-check-lane-fill", _read("settings/hooks.json"))

    def test_dispatch_hook_keeps_only_the_wdrain_gate(self):
        hook = _read("hooks/block-dispatch-over-wdrain.sh")
        for gone in ("cli_dispatch_gate", "lane-overlap", "OVERLAP-BYPASS",
                     "sequential"):
            self.assertNotIn(gone, hook)
        self.assertIn("WDRAIN-BYPASS", hook)


class TestFleetAndResolver(unittest.TestCase):
    def test_no_mode_key_in_any_window(self):
        self.assertFalse(hasattr(cli_fleet, "WINDOW_MODES"))
        for entry in cli_fleet.REMOTE_HOSTS:
            for w in entry.get("windows") or []:
                self.assertNotIn("mode", w, (entry.get("name"), w))

    def test_resolver_keeps_only_the_role(self):
        for name in ("resolve_mode", "read_project_mode", "resolve_concurrency",
                     "dispatch_gate_line", "DEFAULT_MODE",
                     "concurrency_status_row"):
            self.assertFalse(hasattr(cli_concurrency, name), name)
        for name in ("resolve_role", "is_exact_declared_window",
                     "goal_status_row", "_match_window"):
            self.assertTrue(hasattr(cli_concurrency, name), name)

    def test_role_still_resolves(self):
        wins = [{"name": "gk-infra", "cwd": "/srv/x-infra", "role": "infra"}]
        self.assertEqual(cli_concurrency.resolve_role(
            "/srv/x-infra/sub", windows=wins), "infra")
        self.assertIsNone(cli_concurrency.resolve_role("/srv/y", windows=wins))


class TestAlwaysOnModules(unittest.TestCase):
    def test_no_parallelism_paragraph(self):
        text = _read("modules/core/claude-code-tooling.md")
        self.assertNotIn("Parallelism is the working model's decision", text)
        self.assertNotIn("mode: parallel", text)
        self.assertNotIn("one issue at a time", text)

    def test_no_worktree_worker_bullet(self):
        text = _read("modules/git/two-branch-workflow.md")
        self.assertNotIn("SEQUENTIAL", text)
        self.assertNotIn("mode: parallel", text)
        self.assertIn("bgIsolation", text)


class TestAccountBlockPingKept(unittest.TestCase):
    """The account-block owner ping lived inside the removed lane-occupancy
    branch; it moved out intact and still runs for every armed pane."""
    CWD = "/home/newlevel/devel/alim1137"
    SID = "sess-alim-1137"

    def test_wiring(self):
        self.assertFalse(hasattr(goal, "goal_lane_occupancy_nudge"))
        self.assertIn("goal_account_limit_check(",
                      inspect.getsource(goal.goal_lane_sweep))
        self.assertIn("_account_limit_notify_owner(",
                      inspect.getsource(goal.goal_account_limit_check))

    def _run(self, err, rec):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        pings = []

        def send(body, **kw):
            pings.append((body, kw))
            return "sent"

        with m.patch.object(wd, "transcript_last_error", return_value=err):
            logs = goal.goal_account_limit_check(
                100000, tmux, rec, self.SID, self.CWD, "111", tpath, "loc",
                send, False, captured=GOAL_ARMED_CAP)
        return logs, pings, tmux

    def test_monthly_block_pings_owner_once(self):
        rec = {}
        logs, pings, tmux = self._run(MONTHLY, rec)
        self.assertEqual(len(pings), 1, pings)
        self.assertIn("acctblock:", pings[0][1].get("dedup_key", ""))
        self.assertTrue(any("skip:account-limit" in ln for ln in logs), logs)
        self.assertEqual(rec["alim"]["first_seen"], 100000)
        self.assertEqual(tmux.sent, [])
        logs2, pings2, _ = self._run(MONTHLY, rec)
        self.assertEqual(pings2, [])

    def test_weekly_block_defers_to_job6(self):
        logs, pings, _ = self._run(WEEKLY, {})
        self.assertEqual(pings, [])
        self.assertTrue(any("skip:account-limit" in ln for ln in logs), logs)

    def test_recovered_transcript_clears(self):
        rec = {"alim": {"first_seen": 99000, "resets_at": None}}
        self._run("", rec)
        self.assertNotIn("alim", rec)


if __name__ == "__main__":
    unittest.main()
