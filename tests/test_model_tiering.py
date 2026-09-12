"""#991 — SOTA subagent selection. The tiering DOCTRINE is REMOVED, not toggled.

Owner directive 2026-09-11 (verbatim): "potrebujem aby si spravil review toho
ako sa rozhoduju subagenti a ich modely a aj pocet. Chcel by som to cele mat
extrahovane a vediet to vypnut. ... nechavat stale viac harnessu na claude cli
ktore je k tomu optimalizovane" and "daj si zalezat aby to bolo by design
spravne, uz mam dost tvojich patchworkov".

The choice of a subagent's TYPE, MODEL and COUNT is the working model's, resolved
by Claude Code natively. airuleset contributes exactly two things:

  1. a fleet DEFAULT subagent model via the native env
     CLAUDE_CODE_SUBAGENT_MODEL = MODEL_TIERS["opus"] (claude-opus-4-8), no
     _FORCE — the native precedence (per-dispatch model -> agent frontmatter ->
     env -> main) stays, so main overrides by its own judgment;
  2. a BAN of Opus 5 (BANNED_MODELS) enforced by one small dispatch hook
     (block-banned-model.sh) + settings self-heal + Job 41 audit.

Review of subagent work is done by the MAIN Fable before integration (the main
review gate in skills/autopilot/SKILL.md), NOT a worker-internal fable-advisor
phase. Everything else about tiering (phase table, pinned tier agents, the
budget gate, situational injections, the review-tier hook) is DELETED — the
native default is turned off by removing one env var.

The pre-#991 history (phase split, gated Fable, per-phase table) is preserved
verbatim only in .claude/rules-reference/model-awareness-history.md.
"""
import json
import os
import re
import unittest
from unittest import TestCase

import airuleset

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestModelTiers(TestCase):
    def test_four_exact_tiers(self):
        self.assertEqual(set(airuleset.MODEL_TIERS),
                         {"fable", "opus", "sonnet", "haiku"})

    def test_opus_tier_is_4_8(self):
        self.assertEqual(airuleset.MODEL_TIERS["opus"], "claude-opus-4-8")

    def test_managed_main_model_is_fable(self):
        self.assertTrue(airuleset.MANAGED_MODEL.startswith("claude-fable-5-1"))


class TestBannedModels(TestCase):
    """The ban-list is the DISPATCH-surface / audit source of truth (#991)."""

    def test_opus5_and_alias_banned(self):
        self.assertIn("claude-opus-5", airuleset.BANNED_MODELS)
        self.assertIn("opus", airuleset.BANNED_MODELS)
        self.assertIn("opusplan", airuleset.BANNED_MODELS)

    def test_allowed_tiers_not_in_banlist(self):
        for m in ("claude-opus-4-8", "claude-sonnet-5",
                  "claude-haiku-4-5", "claude-fable-5-1", "sonnet", "haiku"):
            self.assertNotIn(m, airuleset.BANNED_MODELS)


class TestAgentNames(TestCase):
    """Only the two claude-opus-4-8-pinned-by-env worker agents survive — the
    fable-advisor / sonnet-implementer / sonnet-mechanical tier agents are
    deleted (native selection replaces them)."""

    def test_only_two_agents(self):
        self.assertEqual(airuleset.AGENT_NAMES,
                         ["autopilot-worker", "ticket-validator"])

    def test_agent_files_on_disk_match(self):
        adir = os.path.join(REPO, "agents")
        on_disk = {f[:-3] for f in os.listdir(adir) if f.endswith(".md")}
        self.assertEqual(on_disk, set(airuleset.AGENT_NAMES))


class TestSubagentModelDefault(TestCase):
    """Lock 1 — the native fleet default, without a _FORCE variant."""

    def _settings(self):
        import cli_config
        return cli_config.apply_managed_settings_defaults({})

    def test_env_subagent_model_is_opus_tier(self):
        env = self._settings()["env"]
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"],
                         airuleset.MODEL_TIERS["opus"])

    def test_no_force_variant(self):
        env = self._settings()["env"]
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", env)

    def test_coexists_with_max_subagents(self):
        env = self._settings()["env"]
        self.assertIn("CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION", env)
        self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL", env)


class TestNoModelFrontmatter(TestCase):
    """Lock 2 — no agent definition pins a model; the env default carries it and
    main overrides natively."""

    def test_no_agent_md_has_model_frontmatter(self):
        adir = os.path.join(REPO, "agents")
        for fn in sorted(os.listdir(adir)):
            if not fn.endswith(".md"):
                continue
            text = open(os.path.join(adir, fn), encoding="utf-8").read()
            m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            fm = m.group(1) if m else ""
            self.assertNotRegex(fm, r"(?m)^\s*model:",
                                "%s carries a model: frontmatter" % fn)


class TestTieringTokenScan(TestCase):
    """Lock 3 — the removed-doctrine tokens do not occur anywhere in the repo
    except the history/audit archives (which preserve the record verbatim)."""

    TOKENS = ["sonnet-implementer", "sonnet-mechanical", "fable-advisor",
              "fable-gate", "fable_gate", "reviewed-by-tier",
              "model-awareness-deep"]
    _ALLOWED_PREFIXES = (".claude/rules-reference/", "audits/")
    _ALLOWED_FILES = {"docs/autopilot-log.md"}
    _SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache"}
    _SCAN_EXT = (".py", ".sh", ".md", ".conf", ".json", ".profile",
                 ".txt", ".yml", ".yaml")

    def test_tokens_absent_outside_history(self):
        pat = re.compile("|".join(re.escape(t) for t in self.TOKENS))
        me = os.path.abspath(__file__)
        offenders = []
        for root, dirs, files in os.walk(REPO):
            # Skip build/vcs dirs AND a nested `.claude/worktrees` tree — exclude
            # by dir NAME, never by path substring (REPO itself may live under
            # `.claude/worktrees/agent-*`, so a substring test would prune the
            # whole walk).
            dirs[:] = [d for d in dirs
                       if d not in self._SKIP_DIRS and d != "worktrees"]
            for f in files:
                p = os.path.join(root, f)
                if p == me or not f.endswith(self._SCAN_EXT):
                    continue
                rel = os.path.relpath(p, REPO)
                if rel in self._ALLOWED_FILES:
                    continue
                if rel.startswith(self._ALLOWED_PREFIXES):
                    continue
                try:
                    text = open(p, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                if pat.search(text):
                    offenders.append(rel)
        self.assertEqual(sorted(offenders), [],
                         "removed tiering tokens still present in: %s"
                         % sorted(offenders))


class TestModelTiersJSON991(TestCase):
    """#991 — `airuleset.py model-tiers --json` exposes the allowlist for an
    external gate (odoo-erp #6935) to read from the SINGLE source of truth."""

    def test_json_shape_matches_constants(self):
        import subprocess
        import sys
        r = subprocess.run(
            [sys.executable, "airuleset.py", "model-tiers", "--json"],
            capture_output=True, text=True, timeout=15, cwd=REPO)
        self.assertEqual(0, r.returncode, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data.get("tiers"), dict(airuleset.MODEL_TIERS))
        self.assertEqual(data.get("banned"), sorted(airuleset.BANNED_MODELS))


class TestWorkerDocSelfReviewModel991(TestCase):
    """#991 — the worker hand-off recipe passes --self-review-model."""

    def test_worker_doc_has_self_review_model(self):
        p = os.path.join(REPO, "agents", "autopilot-worker.md")
        text = open(p, encoding="utf-8").read()
        self.assertIn("--self-review-model", text)


class TestHooksJsonClean(TestCase):
    """Lock 6 — the deleted hooks are gone from hooks.json; the new ban hook is
    wired; the file is valid JSON."""

    def _text(self):
        return open(os.path.join(REPO, "settings", "hooks.json"),
                    encoding="utf-8").read()

    def test_valid_json(self):
        json.loads(self._text())

    def test_deleted_hooks_absent(self):
        text = self._text()
        for h in ("block-unpinned-model-dispatch.sh",
                  "pre-agent-validate-subagent-type.sh",
                  "subagent-stop-check-review-tier.sh"):
            self.assertNotIn(h, text, "hooks.json still references %s" % h)

    def test_ban_hook_wired(self):
        self.assertIn("block-banned-model.sh", self._text())


if __name__ == "__main__":
    unittest.main()
