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
import subprocess
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
                    ".claude/lane-resources.json"):
            self.assertFalse((REPO / rel).exists(), rel)

    def test_lane_fill_hook_not_registered(self):
        self.assertNotIn("stop-check-lane-fill", _read("settings/hooks.json"))

    def test_lane_fill_stub_is_a_silent_noop(self):
        # A session started before the install still calls the old Stop hook
        # path; a missing file is exit 127 on every Stop. One release keeps a
        # no-op stub there (never registered).
        stub = REPO / "hooks" / "stop-check-lane-fill.sh"
        self.assertTrue(stub.exists())
        r = subprocess.run(["bash", str(stub)], input='{"stop_hook_active": false}',
                           capture_output=True, text=True, timeout=10)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertIn("delete in 0.1.557", stub.read_text())

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

    def test_worktree_lanes_stay_legal(self):
        # Review B1: without the worker bullet the module read "any other
        # branch is a mistake and should be deleted" and "one active worker
        # per repo" unqualified, which forbids (and invites deleting) a live
        # subagent worktree lane. One mechanics sentence, module still smaller.
        text = _read("modules/git/two-branch-workflow.md")
        self.assertIn('isolation: "worktree"', text)
        self.assertIn("only once merged", text)
        self.assertIn("one writer at a time", text)
        self.assertNotIn("should be deleted", text)
        self.assertNotIn("one active worker per repo", text)
        self.assertLess(len(text.encode()), 1980)


class TestNoForcedLanes(unittest.TestCase):
    """Review B2/B4/B6/B7: nothing forces parallel or serial lanes, and the
    bundling doctrine is an encouragement, never a mandate."""

    def test_bundle_sentence_is_plain_encouragement(self):
        self.assertEqual(goal_registry._BUNDLE,
                         "related tickets: work them together in one branch, "
                         "one PR and one CI cycle when their focus allows;")
        self.assertNotRegex(goal_registry._BUNDLE,
                            r"(?i)\b(must|always|never|only|required)\b")

    def test_master_never_forces_concurrency(self):
        text = _read("skills/autopilot-master/SKILL.md")
        for banned in ("ALL LANES CONCURRENTLY", "never round-robin",
                       "All lanes run in parallel", "in parallel — never one",
                       "run concurrently throughout", "CONCURRENTLY, never"):
            self.assertNotIn(banned, text, banned)

    def test_worker_docs_carry_no_round_or_default_isolation(self):
        for rel in ("agents/autopilot-worker.md", "skills/autopilot/SKILL.md"):
            text = _read(rel)
            for banned in ("SAME-ROUND", "round's siblings", "Fleet/worktree mode",
                           "Worktree/fleet mode", "Serial fallback",
                           "serial-fallback", "the default — see WORKTREE",
                           "worktree isolation is the default", "worktree-fleet",
                           "never held for the fleet", "on the DEFAULT dispatch",
                           "default `isolation: \"worktree\"`"):
                self.assertNotIn(banned, text, (rel, banned))
            self.assertIn("one writer at a time", text, rel)

    def test_batch_doctrine_is_encouragement(self):
        mod = _read("modules/quality/autonomous-batch-issue-development.md")
        self.assertNotIn("Core invariants", mod)
        self.assertNotIn("bundle bundle-safe issues on ONE dev branch", mod)
        for kept in ("when their focus allows", "never prompt between issues",
                     "single feature = single PR"):
            self.assertIn(kept, mod)
        self.assertLessEqual(len(mod.encode()), 1212)
        skill = _read("skills/batch-issue-development/SKILL.md")
        self.assertNotIn("batch by default", skill)
        self.assertNotIn("Splitting is the exception, never the default", skill)
        self.assertIn("when their focus allows", skill)


class TestNoForcedDelegation(unittest.TestCase):
    """Review B3: the pre-answered "subagent or inline?" question is still never
    asked, but the answer is Claude's own call, not "always subagent"."""
    HOOK = REPO / "hooks" / "pre-ask-auto-answer.sh"

    def test_pre_ask_blocks_and_leaves_the_choice_to_claude(self):
        q = "Two execution options: Subagent-Driven or Inline Execution?"
        payload = {"tool_name": "AskUserQuestion",
                   "tool_input": {"questions": [{"question": q}]}}
        r = subprocess.run(["bash", str(self.HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotRegex(r.stderr, r"(?i)always use subagent")
        self.assertIn("yourself", r.stderr)

    def test_no_surface_mandates_subagents(self):
        for rel in ("hooks/pre-ask-auto-answer.sh",
                    "hooks/stop-check-prose-violations.sh",
                    "skills/ask-before-assuming-deep/DEEP.md"):
            self.assertNotRegex(
                _read(rel), r"(?i)always use subagent|proceed with subagent-driven"
                r"|just dispatch subagents|\| \*\*Subagent\*\* \|", rel)


class TestLiveDocsNameNoRemovedGates(unittest.TestCase):
    """Review B5: live docs and docstrings no longer describe removed gates."""

    def test_stale_sentences_are_gone(self):
        for rel, gone in (
                (".claude/rules/internals-hooks.md", "INDEPENDENCE gate"),
                (".claude/rules/internals-skills-modules.md", "round-3 sequential"),
                (".claude/rules/internals-skills-modules.md", "independence check"),
                (".claude/rules/internals-statusbar.md", "goal_lane_occupancy_nudge"),
                (".claude/rules/internals-scripts.md", "goal_lane_occupancy_nudge"),
                (".claude/rules/internals-cli.md", "stay valid"),
                ("gates/ticketrefs.py", "lane-overlap"),
                ("gates/ticketrefs.py", "block-dispatch-over-wdrain"),
                ("cli_lane_liveness.py", "lane-overlap"),
                ("watchdog/release_gap.py", "goal_lane_occupancy_nudge")):
            self.assertNotIn(gone, _read(rel), (rel, gone))


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

    def _run(self, err, rec, dry_run=False):
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
                send, dry_run, captured=GOAL_ARMED_CAP)
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

    def test_dry_run_never_seeds_the_episode(self):
        # Review A3: a `watchdog --once --dry-run` used to seed rec['alim'];
        # run_once saves state on a dry run, so the real sweep then saw an
        # open episode and never pinged the owner.
        rec = {}
        logs, pings, _ = self._run(MONTHLY, rec, dry_run=True)
        self.assertEqual((rec, pings), ({}, []))
        self.assertTrue(any("skip:account-limit" in ln for ln in logs), logs)
        _, real, _ = self._run(MONTHLY, rec)
        self.assertEqual(len(real), 1, real)

    def test_dry_run_never_clears_an_open_episode(self):
        rec = {"alim": {"first_seen": 99000, "resets_at": None}}
        self._run("", rec, dry_run=True)
        self.assertEqual(rec, {"alim": {"first_seen": 99000, "resets_at": None}})

    def test_ping_names_the_blocked_session(self):
        _, pings, _ = self._run(MONTHLY, {})
        body = pings[0][0]
        self.assertNotIn("dispatch", body)
        self.assertNotIn("pozastaven", body)
        self.assertIn("session", body)


class TestRetiredHelpersGone(unittest.TestCase):
    """Review A6: dead helpers with no reader left after the removal."""

    def test_lane_live_evidence_helper_is_gone(self):
        from watchdog import transcripts
        self.assertFalse(hasattr(transcripts, "lane_has_live_evidence"))
        self.assertFalse(hasattr(wd, "lane_has_live_evidence"))


if __name__ == "__main__":
    unittest.main()
