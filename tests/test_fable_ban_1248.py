"""#1248 — Fable is BANNED as a dispatch model fleet-wide (owner 2026-10-15:
"nechcem aby sa aktualne fable puzival je horsi ako opus 5.5").

Live finding: a gk review subagent ran on Fable because `model=fable` was an
allowed dispatch value (MODEL_TIERS) and the ban-list held only Opus 5. One
source now says it: Fable leaves MODEL_TIERS and joins BANNED_MODELS, so the
dispatch hook, the Job 41 audit, the handoff self-review check and the
`model-tiers` JSON all refuse it. A design stamped by the pre-#1119 Fable main
still dispatches (model_lineup.FORMER_MAIN_MODEL).
"""
import contextlib
import io
import json
import os
import re
import subprocess
from types import SimpleNamespace
from unittest import TestCase

import airuleset
import model_lineup
from _hook_state_cleanup import MODULE_HOOK_HOME  # noqa: E402  (#1046 hermetic HOME)
from gates import designdispatch
from watchdog.model_audit_job import model_float_audit_job

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "block-banned-model.sh")
MODULE = os.path.join(REPO, "modules", "core", "model-awareness.md")
FABLE_IDS = ("fable", "Fable", "claude-fable-5-1", "claude-fable-5-1[1m]",
             "CLAUDE-FABLE-5-1", "claude-fable-5", "claude-fable-6")


def _hook(payload):
    r = subprocess.run(["bash", HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, env={**os.environ, "HOME": MODULE_HOOK_HOME})
    return r.returncode, r.stderr


class TestLineup(TestCase):
    def test_fable_is_not_an_allowed_tier(self):
        self.assertNotIn("fable", airuleset.MODEL_TIERS)
        self.assertNotIn("claude-fable-5-1", airuleset.MODEL_TIERS.values())

    def test_fable_ids_on_the_ban_list(self):
        for m in ("fable", "claude-fable-5", "claude-fable-5-1"):
            self.assertIn(m, airuleset.BANNED_MODELS)

    def test_main_model_unchanged(self):
        self.assertEqual(airuleset.MANAGED_MODEL, "claude-opus-5-5[1m]")
        self.assertNotIn("claude-opus-5-5", airuleset.BANNED_MODELS)

    def test_former_main_kept_by_name_only(self):
        self.assertEqual(model_lineup.FORMER_MAIN_MODEL, "claude-fable-5-1")


class TestPredicates(TestCase):
    def test_dispatch_predicate_bans_every_fable_shape(self):
        for v in FABLE_IDS:
            self.assertTrue(airuleset.is_banned_model(v), v)
            self.assertFalse(airuleset.is_allowed_model(v), v)

    def test_audit_predicate_flags_served_fable(self):
        for v in ("claude-fable-5-1", "claude-fable-5-1[1m]",
                  "claude-fable-5-1-20261001", "us.anthropic.claude-fable-5-1",
                  "claude-fable-5", "claude-fable-6"):
            self.assertTrue(airuleset.is_banned_model_for_audit(v), v)

    def test_allowed_models_still_clear(self):
        for v in ("claude-opus-5-5[1m]", "claude-opus-5-5", "claude-sonnet-5-5",
                  "claude-opus-4-8", "claude-haiku-4-5"):
            self.assertFalse(airuleset.is_banned_model(v), v)
            self.assertFalse(airuleset.is_banned_model_for_audit(v), v)


class TestDispatchHook(TestCase):
    def test_agent_dispatch_on_fable_blocked(self):
        for v in FABLE_IDS:
            rc, err = _hook({"tool_name": "Agent", "tool_input": {"model": v}})
            self.assertEqual(rc, 2, (v, err))
            self.assertIn("#1248", err)

    def test_workflow_script_on_fable_blocked(self):
        script = "export const meta = {name: 'x'}\nawait agent('r', {model: 'fable'})\n"
        rc, err = _hook({"tool_name": "Workflow", "tool_input": {"script": script}})
        self.assertEqual(rc, 2, err)

    def test_allowed_dispatch_passes(self):
        for v in ("claude-opus-5-5", "sonnet", "claude-sonnet-5-5", "haiku"):
            rc, err = _hook({"tool_name": "Agent", "tool_input": {"model": v}})
            self.assertEqual(rc, 0, (v, err))
        rc, err = _hook({"tool_name": "Agent", "tool_input": {"prompt": "x"}})
        self.assertEqual(rc, 0, err)


class TestEveryConsumer(TestCase):
    def test_handoff_self_review_on_fable_refused(self):
        self.assertIsNone(airuleset._canonical_self_review_model("claude-fable-5-1"))
        self.assertIsNone(airuleset._canonical_self_review_model("fable"))

    def test_model_tiers_json_moves_fable_to_banned(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            airuleset.cmd_model_tiers(SimpleNamespace())
        data = json.loads(out.getvalue())
        self.assertNotIn("claude-fable-5-1", data["tiers"].values())
        self.assertIn("claude-fable-5-1", data["banned"])

    def test_job41_journals_a_fable_subagent(self):
        find = lambda projects_dir, cwd: ("/t/r.jsonl", 0.0)  # noqa: E731
        models = {"/t/r.jsonl": "claude-opus-5-5", "/t/r.jsonl.sub": "claude-fable-5-1"}
        out = model_float_audit_job(0.0, {}, [("%p", "/r")], "/proj",
                                    lambda p: models.get(str(p), ""), find,
                                    lambda main, now: [str(main) + ".sub"],
                                    due_fn=lambda *a, **k: True)
        self.assertEqual(len(out), 1, out)
        self.assertIn("claude-fable-5-1", out[0])

    def test_old_fable_design_stamp_still_dispatches(self):
        ids = designdispatch._accepted_main_ids()
        self.assertIn(designdispatch._norm_model("claude-fable-5-1"), ids)
        self.assertIn(designdispatch._norm_model("claude-opus-5-5"), ids)


class TestDoctrine(TestCase):
    def setUp(self):
        with open(MODULE, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_fable_not_listed_as_allowed(self):
        allowed = re.search(r"Also allowed:(.*?)\.(?:\s|$)", self.text)  # "5.5" is no stop
        self.assertIsNotNone(allowed)
        self.assertNotIn("fable", allowed.group(1).lower())

    def test_fable_named_banned_with_the_reason(self):
        ban = self.text[self.text.index("BANNED"):]
        self.assertIn("**Fable**", ban)
        self.assertIn("worse than Opus 5.5", ban)
        self.assertIn("#1248", ban)


class TestReviewFixes(TestCase):
    """#1248 review round: the Workflow scan reads only a real `model` option,
    in every shape a script writes it; the Job 41 remedy and the doctrine never
    point at Fable; the design-by audit counts the Opus 5.5 main compliant."""

    def _wf(self, script):
        return _hook({"tool_name": "Workflow", "tool_input": {"script": script}})

    def test_workflow_option_shapes_blocked(self):
        for script in ('agent("r", {"model": "fable"})', "agent('r', {model : 'fable'})",
                       "opts.model = 'fable'\nawait agent('r', opts)",
                       "agent('r', {\n  model:\n    'claude-fable-5-1'\n})",
                       "agent('r', {model: 'global.anthropic.claude-fable-5-1'})"):
            rc, err = self._wf(script)
            self.assertEqual(rc, 2, (script, err))

    def test_agent_rare_fable_spellings_blocked(self):
        for v in ("anthropic/claude-fable-5-1", "fable-5.1", "fable5",
                  "global.anthropic.claude-fable-5-1"):
            rc, err = _hook({"tool_name": "Agent", "tool_input": {"model": v}})
            self.assertEqual(rc, 2, (v, err))

    def test_prompt_text_naming_fable_is_not_a_model_option(self):
        script = ("await agent('Write the hand-off. Self-review-model: fable is the "
                  "odoo-erp template line.', {model: 'claude-opus-5-5'})")
        rc, err = self._wf(script)
        self.assertEqual(rc, 0, err)

    def test_job41_remedy_names_the_managed_model(self):
        find = lambda projects_dir, cwd: ("/t/r.jsonl", 0.0)  # noqa: E731
        out = model_float_audit_job(0.0, {}, [("%p", "/r")], "/proj",
                                    lambda p: "claude-fable-5-1", find,
                                    lambda main, now: [], due_fn=lambda *a, **k: True)
        self.assertEqual(len(out), 1, out)
        self.assertIn("/model -> %s" % airuleset.MANAGED_MODEL, out[0])
        import inspect
        import cli_model_audit
        self.assertNotIn("-> Fable", inspect.getsource(cli_model_audit))

    def test_design_by_audit_counts_the_opus55_main_compliant(self):
        from test_audit_design_by_1061 import _make_runner, ab  # puts scripts/ on sys.path
        runner = _make_runner([(1, "t1"), (2, "t2")], {
            1: [{"body": "Design-by: main claude-opus-5-5", "created_at": "2026-10-15T10:00:00Z"}],
            2: [{"body": "Design-by: main claude-opus-4-8", "created_at": "2026-10-15T10:00:00Z"}]})
        v = ab.audit_design_by("o/r", issues_runner=runner, comments_runner=runner)
        self.assertEqual([x["number"] for x in v], [2])

    def test_doctrine_never_calls_the_main_or_its_review_fable(self):
        stale = re.compile(r"Fable (main|MAIN|review|id)|\(Fable\)|Fable MAIN")
        for rel in ("modules/quality/architecture-first.md", "skills/autopilot/SKILL.md",
                    "agents/implementer.md", "agents/autopilot-worker.md",
                    "skills/completion-report-deep/DEEP.md", "skills/meeting-analysis/SKILL.md",
                    "goal_registry.py"):
            with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
                hits = [i for i, ln in enumerate(fh, 1) if stale.search(ln)]
            self.assertEqual(hits, [], rel)
