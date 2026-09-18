"""Target-governance inventory + shadow guard + daily delta (#874).

The fleet review never inventoried what a TARGET project adds to its OWN
`.claude/` (slash commands, skills, hooks, rules, settings hooks, CLAUDE.md
@imports). A project `.claude/commands/resume.md` shadowed the built-in
`/resume` for 67 days, invisible. This extends the review with:

  1. cli_mdreview_audit.target_governance() — per-project inventory with git
     provenance (via an injected git_fn, hermetic) + a name classifier.
  2. the daily target-governance delta on the pinned ticket (watchdog Job 43).
  3. gates.commandshadow + hooks/block-builtin-command-shadow.sh — a PreToolUse
     Write|Edit guard that refuses a `.claude/commands/<builtin>.md` /
     `.claude/skills/<builtin>/SKILL.md` path.

All fixtures are hermetic (temp repos + a fake git_fn + a fake previous
snapshot + the hook driven through its stdin contract) — never the real
~/.claude, a real gh call, or a real SSH sweep.
"""
import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

REPO = Path(__file__).resolve().parent.parent

def FAKE_GIT(pd, rel):
    return ("abc1234", "2026-07-13", "some.dev")


def _mk_project(root, name):
    """Create a fixture project dir with a .claude/ tree. Returns its Path."""
    pd = Path(root) / name
    (pd / ".claude" / "commands").mkdir(parents=True)
    (pd / ".claude" / "skills").mkdir(parents=True)
    (pd / ".claude" / "hooks").mkdir(parents=True)
    (pd / ".claude" / "rules").mkdir(parents=True)
    return pd


# ---------------------------------------------------------------------------
# The pinned built-in constant + classifier constants
# ---------------------------------------------------------------------------

class TestBuiltinConstant(unittest.TestCase):

    def test_resume_and_clear_are_builtins(self):
        import cli_mdreview_audit as a
        low = {c.lower() for c in a.CLAUDE_CODE_BUILTIN_COMMANDS}
        self.assertIn("resume", low)
        self.assertIn("clear", low)
        self.assertIn("compact", low)

    def test_pause_is_not_a_builtin(self):
        import cli_mdreview_audit as a
        low = {c.lower() for c in a.CLAUDE_CODE_BUILTIN_COMMANDS}
        self.assertNotIn("pause", low)
        self.assertNotIn("drift-guard", low)

    def test_source_records_the_cc_version(self):
        import cli_mdreview_audit as a
        src = a.CLAUDE_CODE_BUILTIN_COMMANDS_SOURCE
        self.assertIsInstance(src, str)
        self.assertIn("2.1", src, "source must record the CC version it was read from")

    def test_class_constants_exist(self):
        import cli_mdreview_audit as a
        self.assertEqual(a.CLASS_BUILTIN_COLLISION, "BUILTIN-COLLISION")
        self.assertEqual(a.CLASS_MANAGED_DUPLICATE, "MANAGED-DUPLICATE")
        self.assertEqual(a.CLASS_UNREVIEWED, "UNREVIEWED")
        self.assertEqual(a.CLASS_RULE_SHAPE, "RULE-SHAPE")

    def test_continue_is_a_builtin_vim_is_not(self):
        # #874 review B: `continue` (resume's alias) MUST be present; `vim`
        # (not a real slash command) MUST be absent.
        import cli_mdreview_audit as a
        low = {c.lower() for c in a.CLAUDE_CODE_BUILTIN_COMMANDS}
        self.assertIn("continue", low)
        self.assertNotIn("vim", low)

    def test_gate_fallback_in_sync_with_ssot(self):
        # The gate ships a fallback list for when the SSOT import breaks; it
        # must stay identical to cli_mdreview_audit.CLAUDE_CODE_BUILTIN_COMMANDS.
        import builtins
        import cli_mdreview_audit as a
        from gates import commandshadow
        # Force the fallback branch by making the SSOT import fail.
        real_import = builtins.__import__

        def boom(name, *args, **kwargs):
            if name == "cli_mdreview_audit":
                raise ImportError("forced")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=boom):
            fallback = commandshadow._builtins_lower()
        ssot = {c.lower() for c in a.CLAUDE_CODE_BUILTIN_COMMANDS}
        self.assertEqual(fallback, ssot,
                         "gate fallback list drifted from the SSOT")


# ---------------------------------------------------------------------------
# target_governance() — inventory with provenance + classification
# ---------------------------------------------------------------------------

class TestTargetGovernanceInventory(unittest.TestCase):

    def _items(self, pd, kind):
        import cli_mdreview_audit as a
        tg = a.target_governance(pd, git_fn=FAKE_GIT)
        return [it for it in tg["items"] if it["kind"] == kind]

    def test_command_provenance_from_git_fn(self):
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "camera-box")
            (pd / ".claude" / "commands" / "drift-guard.md").write_text(
                "# Drift guard\nrun it\n", encoding="utf-8")
            cmds = self._items(pd, "command")
            self.assertEqual(len(cmds), 1)
            it = cmds[0]
            self.assertEqual(it["name"], "drift-guard")
            self.assertEqual(it["repo"], "camera-box")
            self.assertEqual(it["provenance"]["sha"], "abc1234")
            self.assertEqual(it["provenance"]["date"], "2026-07-13")
            self.assertEqual(it["provenance"]["author"], "some.dev")

    def test_resume_command_is_builtin_collision(self):
        import cli_mdreview_audit as a
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "odoo-erp")
            (pd / ".claude" / "commands" / "resume.md").write_text(
                "# resume\n", encoding="utf-8")
            cmds = self._items(pd, "command")
            self.assertEqual(len(cmds), 1)
            self.assertIn(a.CLASS_BUILTIN_COLLISION, cmds[0]["classes"])

    def test_non_builtin_command_has_no_collision_class(self):
        import cli_mdreview_audit as a
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "odoo-erp")
            (pd / ".claude" / "commands" / "resume-servers.md").write_text(
                "# resume servers\n", encoding="utf-8")
            cmds = self._items(pd, "command")
            self.assertEqual(len(cmds), 1)
            self.assertNotIn(a.CLASS_BUILTIN_COLLISION, cmds[0]["classes"])

    def test_skill_named_like_airuleset_skill_is_managed_duplicate(self):
        import cli_mdreview_audit as a
        # pick a real managed skill name that ships in this repo
        managed = sorted(p.name for p in (REPO / "skills").iterdir()
                         if (p / "SKILL.md").exists())
        self.assertTrue(managed, "repo must ship at least one managed skill")
        dup_name = managed[0]
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            sd = pd / ".claude" / "skills" / dup_name
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                "---\nname: x\ndescription: y\n---\nbody\n", encoding="utf-8")
            skills = self._items(pd, "skill")
            self.assertEqual(len(skills), 1)
            self.assertIn(a.CLASS_MANAGED_DUPLICATE, skills[0]["classes"])

    def test_project_specific_skill_not_managed_duplicate(self):
        import cli_mdreview_audit as a
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "voiceagent")
            sd = pd / ".claude" / "skills" / "call-review-xyz-unique"
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                "---\nname: x\ndescription: y\n---\nbody\n", encoding="utf-8")
            skills = self._items(pd, "skill")
            self.assertEqual(len(skills), 1)
            self.assertNotIn(a.CLASS_MANAGED_DUPLICATE, skills[0]["classes"])

    def test_skill_named_like_builtin_is_builtin_collision(self):
        import cli_mdreview_audit as a
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            sd = pd / ".claude" / "skills" / "clear"
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                "---\nname: x\ndescription: y\n---\nbody\n", encoding="utf-8")
            skills = self._items(pd, "skill")
            self.assertIn(a.CLASS_BUILTIN_COLLISION, skills[0]["classes"])

    def test_rule_bad_frontmatter_is_rule_shape(self):
        import cli_mdreview_audit as a
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            # a paths-less always-on rule is fine; a rule that CLAIMS
            # frontmatter but is malformed is RULE-SHAPE
            (pd / ".claude" / "rules" / "broken.md").write_text(
                "---\nname: broken\n(no closing frontmatter fence)\n",
                encoding="utf-8")
            rules = self._items(pd, "rule")
            self.assertEqual(len(rules), 1)
            self.assertIn(a.CLASS_RULE_SHAPE, rules[0]["classes"])

    def test_rule_valid_paths_frontmatter_not_rule_shape(self):
        import cli_mdreview_audit as a
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            (pd / ".claude" / "rules" / "ok.md").write_text(
                "---\npaths:\n  - 'src/**'\n---\nbody\n", encoding="utf-8")
            rules = self._items(pd, "rule")
            self.assertEqual(len(rules), 1)
            self.assertNotIn(a.CLASS_RULE_SHAPE, rules[0]["classes"])
            self.assertTrue(rules[0]["detail"]["has_paths"])

    def test_rule_named_like_managed_rule_is_managed_duplicate(self):
        import cli_mdreview_audit as a
        managed = sorted(p.name for p in (REPO / "rules").glob("*.md"))
        self.assertTrue(managed, "repo must ship at least one managed rule")
        dup = managed[0]
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            (pd / ".claude" / "rules" / dup).write_text(
                "---\npaths:\n  - 'x/**'\n---\nbody\n", encoding="utf-8")
            rules = self._items(pd, "rule")
            self.assertEqual(len(rules), 1)
            self.assertIn(a.CLASS_MANAGED_DUPLICATE, rules[0]["classes"])

    def test_frontmatter_horizontal_rule_not_flagged(self):
        import cli_mdreview_audit as a
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            # a doc that OPENS with a `---` markdown horizontal rule (no YAML
            # key) is NOT frontmatter → must NOT be RULE-SHAPE (#874 review A)
            (pd / ".claude" / "rules" / "hr.md").write_text(
                "---\n\nSome prose after a horizontal rule.\n", encoding="utf-8")
            rules = self._items(pd, "rule")
            self.assertEqual(len(rules), 1)
            self.assertNotIn(a.CLASS_RULE_SHAPE, rules[0]["classes"])

    def test_settings_hook_airuleset_fork_not_treated_as_managed(self):
        # a naive `"devel/airuleset" in cmd` substring wrongly treats a sibling
        # `~/devel/airuleset-fork/...` as managed and hides it (#874 review A2).
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            settings = {"hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command",
                     "command": "bash ~/devel/airuleset-fork/hooks/x.sh"},
                ]},
            ]}}
            (pd / ".claude" / "settings.json").write_text(
                json.dumps(settings), encoding="utf-8")
            import cli_mdreview_audit as a
            tg = a.target_governance(pd, git_fn=FAKE_GIT)
            cmds = [it["detail"]["command"]
                    for it in tg["items"] if it["kind"] == "settings-hook"]
            self.assertTrue(any("airuleset-fork" in c for c in cmds),
                            f"a fork path must NOT be hidden as managed: {cmds}")

    def test_project_hook_files_inventoried(self):
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            (pd / ".claude" / "hooks" / "my-hook.sh").write_text(
                "#!/usr/bin/env bash\n", encoding="utf-8")
            hooks = self._items(pd, "hook")
            self.assertEqual(len(hooks), 1)
            self.assertEqual(hooks[0]["name"], "my-hook.sh")

    def test_unmanaged_settings_hook_surfaced_managed_skipped(self):
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            settings = {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [
                            # managed — points at the airuleset checkout
                            {"type": "command",
                             "command": "bash ~/devel/airuleset/hooks/x.sh"},
                            # project-owned — must be surfaced
                            {"type": "command",
                             "command": "bash .claude/hooks/my-hook.sh"},
                        ]},
                    ]
                }
            }
            (pd / ".claude" / "settings.json").write_text(
                json.dumps(settings), encoding="utf-8")
            import cli_mdreview_audit as a
            tg = a.target_governance(pd, git_fn=FAKE_GIT)
            sh = [it for it in tg["items"] if it["kind"] == "settings-hook"]
            cmds = [it["detail"]["command"] for it in sh]
            self.assertTrue(any("my-hook.sh" in c for c in cmds),
                            f"project-owned settings hook must surface: {cmds}")
            self.assertFalse(any("devel/airuleset" in c for c in cmds),
                             f"airuleset-managed hook must NOT surface: {cmds}")

    def test_external_claude_md_import_surfaced(self):
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            (pd / "CLAUDE.md").write_text(
                "# proj\n@~/devel/airuleset/modules/core/x.md\n"
                "@.claude/rules/local-thing.md\n", encoding="utf-8")
            import cli_mdreview_audit as a
            tg = a.target_governance(pd, git_fn=FAKE_GIT)
            imports = [it for it in tg["items"] if it["kind"] == "import"]
            names = [it["name"] for it in imports]
            self.assertTrue(any("local-thing" in n for n in names),
                            f"external import must surface: {names}")
            self.assertFalse(any("devel/airuleset" in n for n in names),
                             f"airuleset import must NOT surface: {names}")

    def test_missing_claude_dir_yields_empty_items(self):
        import cli_mdreview_audit as a
        with TemporaryDirectory() as tmp:
            pd = Path(tmp) / "bare"
            pd.mkdir()
            tg = a.target_governance(pd, git_fn=FAKE_GIT)
            self.assertEqual(tg["items"], [])
            self.assertEqual(tg["repo"], "bare")


# ---------------------------------------------------------------------------
# inventory_box wires target_governance in
# ---------------------------------------------------------------------------

class TestInventoryBoxWires(unittest.TestCase):

    def test_inventory_box_has_target_governance(self):
        import cli_mdreview_audit as a
        with TemporaryDirectory() as tmp:
            pd = _mk_project(tmp, "proj")
            (pd / ".claude" / "commands" / "resume.md").write_text(
                "# r\n", encoding="utf-8")
            inv = a.inventory_box([str(pd)], git_fn=FAKE_GIT)
            self.assertIn("target_governance", inv)
            tgs = inv["target_governance"]
            self.assertEqual(len(tgs), 1)
            self.assertEqual(tgs[0]["repo"], "proj")


# ---------------------------------------------------------------------------
# collect + delta
# ---------------------------------------------------------------------------

class TestGovernanceDelta(unittest.TestCase):

    def _audit_with(self, items):
        return {"schema": 1, "date": "2026-09-18",
                "boxes": [{"host": "dev1", "inventory": {
                    "target_governance": [
                        {"dir": "/x/odoo-erp", "repo": "odoo-erp",
                         "items": items}]}}]}

    def _item(self, kind, name, classes, sha="abc1234"):
        return {"repo": "odoo-erp", "kind": kind, "name": name,
                "classes": list(classes),
                "provenance": {"sha": sha, "date": "2026-07-13",
                               "author": "d"},
                "detail": {}}

    def test_collect_flattens_across_boxes(self):
        import cli_mdreview_audit as a
        data = self._audit_with([self._item("command", "resume",
                                            ["BUILTIN-COLLISION"])])
        gmap = a.collect_target_governance(data)
        self.assertEqual(len(gmap), 1)
        (k, v), = gmap.items()
        self.assertEqual(v["name"], "resume")

    def test_first_run_all_new(self):
        import cli_mdreview_audit as a
        data = self._audit_with([self._item("command", "resume",
                                            ["BUILTIN-COLLISION"])])
        gmap = a.collect_target_governance(data)
        delta = a.target_governance_delta(gmap, {}, "2026-09-18")
        self.assertEqual(delta["count"], 1)
        self.assertEqual(delta["collisions"], 1)
        self.assertIsNotNone(delta["comment"])
        self.assertIn("Target-governance delta 2026-09-18", delta["comment"])
        self.assertIn("resume", delta["comment"])
        self.assertIn("BUILTIN-COLLISION", delta["comment"])

    def test_unchanged_no_delta(self):
        import cli_mdreview_audit as a
        data = self._audit_with([self._item("command", "resume",
                                            ["BUILTIN-COLLISION"])])
        gmap = a.collect_target_governance(data)
        snap = a.governance_snapshot(gmap)
        delta = a.target_governance_delta(gmap, snap, "2026-09-18")
        self.assertEqual(delta["count"], 0)
        self.assertIsNone(delta["comment"])

    def test_changed_sha_is_delta(self):
        import cli_mdreview_audit as a
        old = self._audit_with([self._item("command", "resume",
                                           ["BUILTIN-COLLISION"], sha="aaa")])
        snap = a.governance_snapshot(a.collect_target_governance(old))
        new = self._audit_with([self._item("command", "resume",
                                           ["BUILTIN-COLLISION"], sha="bbb")])
        gmap = a.collect_target_governance(new)
        delta = a.target_governance_delta(gmap, snap, "2026-09-18")
        self.assertEqual(delta["count"], 1)

    def test_clean_new_item_shows_unreviewed(self):
        import cli_mdreview_audit as a
        data = self._audit_with([self._item("command", "drift-guard", [])])
        gmap = a.collect_target_governance(data)
        delta = a.target_governance_delta(gmap, {}, "2026-09-18")
        self.assertEqual(delta["collisions"], 0)
        self.assertIn("UNREVIEWED", delta["comment"])


# ---------------------------------------------------------------------------
# gates.commandshadow — the mechanical guard
# ---------------------------------------------------------------------------

class TestCommandShadowGate(unittest.TestCase):

    def _classify(self, file_path, content=""):
        from gates import commandshadow
        return commandshadow.classify(file_path, content)

    def test_resume_command_blocked(self):
        blocked, reason = self._classify(
            "/home/u/devel/odoo-erp/.claude/commands/resume.md", "# r\n")
        self.assertTrue(blocked)
        self.assertIn("resume", reason)

    def test_resume_servers_allowed(self):
        blocked, _ = self._classify(
            "/home/u/devel/odoo-erp/.claude/commands/resume-servers.md", "x")
        self.assertFalse(blocked)

    def test_builtin_skill_blocked(self):
        blocked, _ = self._classify(
            "/home/u/devel/proj/.claude/skills/clear/SKILL.md", "x")
        self.assertTrue(blocked)

    def test_project_skill_allowed(self):
        blocked, _ = self._classify(
            "/home/u/devel/proj/.claude/skills/call-review/SKILL.md", "x")
        self.assertFalse(blocked)

    def test_unrelated_path_allowed(self):
        blocked, _ = self._classify(
            "/home/u/devel/proj/src/resume.md", "x")
        self.assertFalse(blocked)

    def test_bypass_token_allows(self):
        from gates import commandshadow
        # mock _log so the bypass path never writes the real ~/.claude log
        with mock.patch.object(commandshadow, "_log"):
            blocked, _ = self._classify(
                "/home/u/devel/proj/.claude/commands/resume.md",
                "# airuleset:command-shadow-ok legacy alias kept\n# r\n")
        self.assertFalse(blocked)

    def test_continue_alias_blocked(self):
        # `continue` is an ALIAS of the built-in `resume` — the SAME incident
        # class the guard exists for. It MUST be caught (#874 review B).
        blocked, reason = self._classify(
            "/home/u/devel/odoo-erp/.claude/commands/continue.md", "x")
        self.assertTrue(blocked, reason)

    def test_vim_not_blocked_false_positive_removed(self):
        # `vim` is NOT a real 2.1.268 slash command — must not false-block.
        blocked, _ = self._classify(
            "/home/u/devel/proj/.claude/commands/vim.md", "x")
        self.assertFalse(blocked)

    def test_reason_names_rename_convention(self):
        _, reason = self._classify(
            "/home/u/devel/proj/.claude/commands/resume.md", "x")
        self.assertIn("resume-", reason)


# ---------------------------------------------------------------------------
# the hook, driven through its stdin contract
# ---------------------------------------------------------------------------

HOOK = REPO / "hooks" / "block-builtin-command-shadow.sh"


class TestHookStdinContract(unittest.TestCase):

    def setUp(self):
        # Redirect the subprocess hook's audit log into a throwaway HOME so the
        # test never appends to the real ~/.claude/command-shadow-gate.log
        # (#874 review — hermeticity). Subprocess-local env only; no in-process
        # HOME mutation (avoids the #732-class leak into sibling modules).
        self._tmp_home = TemporaryDirectory()
        self.addCleanup(self._tmp_home.cleanup)

    def _run(self, file_path, content=""):
        payload = json.dumps({
            "tool_name": "Write",
            "cwd": "/home/u/devel/proj",
            "tool_input": {"file_path": file_path, "content": content},
        })
        env = dict(os.environ)
        env["HOME"] = self._tmp_home.name
        return subprocess.run(
            ["bash", str(HOOK)], input=payload,
            capture_output=True, text=True, timeout=20, env=env)

    def test_hook_exists_and_executable(self):
        self.assertTrue(HOOK.exists(), f"missing hook: {HOOK}")
        self.assertTrue(os.access(HOOK, os.X_OK), "hook must be executable")

    def test_hook_blocks_resume_command_reason_on_stderr(self):
        r = self._run("/home/u/devel/odoo-erp/.claude/commands/resume.md", "# r")
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout} stderr={r.stderr}")
        self.assertIn("resume", r.stderr.lower())

    def test_hook_allows_renamed_command(self):
        r = self._run("/home/u/devel/odoo-erp/.claude/commands/resume-servers.md", "x")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")

    def test_hook_allows_unrelated_write(self):
        r = self._run("/home/u/devel/proj/README.md", "x")
        self.assertEqual(r.returncode, 0)

    def test_hook_registered_write_and_edit(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        wired = {"Write": False, "Edit": False}
        for block in cfg["hooks"]["PreToolUse"]:
            m = block.get("matcher")
            if m in wired:
                for h in block.get("hooks", []):
                    if "block-builtin-command-shadow.sh" in h.get("command", ""):
                        wired[m] = True
        self.assertTrue(wired["Write"], "hook must be wired on Write")
        self.assertTrue(wired["Edit"], "hook must be wired on Edit")


# ---------------------------------------------------------------------------
# cadence job — daily target-governance delta rides Job 43
# ---------------------------------------------------------------------------

class TestCadenceGovernanceDelta(unittest.TestCase):

    def _tiers_hash(self):
        import airuleset
        import hashlib
        return hashlib.sha1(
            str(sorted(airuleset.MODEL_TIERS.items())).encode()).hexdigest()

    def _audit(self):
        return {"schema": 1, "date": "2026-09-18",
                "boxes": [{"host": "dev1", "inventory": {
                    "target_governance": [
                        {"dir": "/x/odoo-erp", "repo": "odoo-erp", "items": [
                            {"repo": "odoo-erp", "kind": "command",
                             "name": "resume", "classes": ["BUILTIN-COLLISION"],
                             "provenance": {"sha": "ec797d7", "date": "2026-07-13",
                                            "author": "d"}, "detail": {}}]}]}}],
                "failed": [], "skipped": [], "scoping": []}

    def test_daily_delta_posts_comment_and_journals(self):
        from watchdog import mdreview_cadence as mc
        from tempfile import TemporaryDirectory as TD
        now = time.time()
        with TD() as tmp:
            sp = Path(tmp) / "state.json"
            sp.write_text(json.dumps({
                "schema": 1, "ticket": 874,
                "model_tiers_hash": self._tiers_hash(),
                "last_eval_ts": now - 86400 * 2,
                "target_governance": {},
            }), encoding="utf-8")
            gh_calls = []

            def fake_gh(argv):
                gh_calls.append(argv)
                if "view" in str(argv):
                    # OPEN → reopen not-due; daily delta still runs
                    return json.dumps({"state": "open", "closedAt": ""}), 0
                return "", 0

            with mock.patch("socket.gethostname", return_value="dev1"):
                with mock.patch("cli_mdreview_audit.run_fleet",
                                return_value=self._audit()):
                    with mock.patch("cli_mdreview_audit.save_artifact",
                                    return_value="/x/2026-09-18.json"):
                        logs = mc.mdreview_cadence_job(
                            now, {}, state_path=str(sp), gh_runner=fake_gh)

            comment_calls = [c for c in gh_calls if "comment" in str(c)]
            self.assertEqual(len(comment_calls), 1,
                             f"exactly one delta comment expected: {gh_calls}")
            body = " ".join(str(x) for x in comment_calls[0])
            self.assertIn("Target-governance delta", body)
            self.assertIn("resume", body)
            self.assertTrue(any("target-governance" in ln for ln in logs),
                            f"journal must carry target-governance count: {logs}")
            # snapshot persisted for the next diff
            st = json.loads(sp.read_text())
            self.assertTrue(st.get("target_governance"),
                            "snapshot must be stored in cadence state")

    def test_no_delta_journals_only(self):
        from watchdog import mdreview_cadence as mc
        from tempfile import TemporaryDirectory as TD
        import cli_mdreview_audit as a
        now = time.time()
        snap = a.governance_snapshot(a.collect_target_governance(self._audit()))
        with TD() as tmp:
            sp = Path(tmp) / "state.json"
            sp.write_text(json.dumps({
                "schema": 1, "ticket": 874,
                "model_tiers_hash": self._tiers_hash(),
                "last_eval_ts": now - 86400 * 2,
                "target_governance": snap,
            }), encoding="utf-8")
            gh_calls = []

            def fake_gh(argv):
                gh_calls.append(argv)
                if "view" in str(argv):
                    return json.dumps({"state": "open", "closedAt": ""}), 0
                return "", 0

            with mock.patch("socket.gethostname", return_value="dev1"):
                with mock.patch("cli_mdreview_audit.run_fleet",
                                return_value=self._audit()):
                    with mock.patch("cli_mdreview_audit.save_artifact",
                                    return_value="/x/2026-09-18.json"):
                        logs = mc.mdreview_cadence_job(
                            now, {}, state_path=str(sp), gh_runner=fake_gh)

            comment_calls = [c for c in gh_calls if "comment" in str(c)]
            self.assertEqual(len(comment_calls), 0,
                             f"no comment when nothing changed: {gh_calls}")
            self.assertTrue(any("target-governance" in ln for ln in logs))

    def test_dry_run_no_fleet_no_comment(self):
        from watchdog import mdreview_cadence as mc
        from tempfile import TemporaryDirectory as TD
        now = time.time()
        with TD() as tmp:
            sp = Path(tmp) / "state.json"
            sp.write_text(json.dumps({
                "schema": 1, "ticket": 874,
                "model_tiers_hash": self._tiers_hash(),
                "last_eval_ts": now - 86400 * 2,
                "target_governance": {},
            }), encoding="utf-8")
            gh_calls = []

            def fake_gh(argv):
                gh_calls.append(argv)
                if "view" in str(argv):
                    return json.dumps({"state": "open", "closedAt": ""}), 0
                return "", 0

            with mock.patch("socket.gethostname", return_value="dev1"):
                with mock.patch("cli_mdreview_audit.run_fleet",
                                side_effect=AssertionError("run_fleet in dry-run")):
                    mc.mdreview_cadence_job(
                        now, {}, dry_run=True, state_path=str(sp),
                        gh_runner=fake_gh)
            comment_calls = [c for c in gh_calls if "comment" in str(c)]
            self.assertEqual(len(comment_calls), 0, "dry-run posts nothing")


# ---------------------------------------------------------------------------
# run_once / cadence docstring accuracy
# ---------------------------------------------------------------------------

class TestDocstringAccuracy(unittest.TestCase):

    def test_cadence_docstring_mentions_target_governance(self):
        from watchdog import mdreview_cadence
        doc = mdreview_cadence.__doc__ or ""
        self.assertIn("target-governance", doc.lower(),
                      "cadence docstring must document the daily delta")

    def test_job43_docstring_mentions_target_governance(self):
        from watchdog import run_once
        doc = run_once.__doc__ or ""
        self.assertIn("target-governance", doc.lower())


if __name__ == "__main__":
    unittest.main()
