"""Tests for airuleset CLI."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


class TestParseProfile(TestCase):
    def test_universal_profile_parses(self):
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        self.assertGreater(len(entries), 0)
        for entry in entries:
            self.assertTrue(
                entry.startswith("modules/") or entry.startswith("rules/"),
                f"Unexpected entry: {entry}",
            )

    def test_all_profile_entries_exist(self):
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        for entry in entries:
            full_path = airuleset.REPO_DIR / entry
            self.assertTrue(full_path.exists(), f"Missing: {entry}")

    def test_rust_windows_profile_includes_universal(self):
        rw_entries = airuleset.parse_profile(
            airuleset.REPO_DIR / "profiles" / "rust-windows.profile"
        )
        uni_entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        # All universal entries should be in rust-windows
        for entry in uni_entries:
            self.assertIn(entry, rw_entries, f"Missing from rust-windows: {entry}")
        # rust-windows should have more
        self.assertGreater(len(rw_entries), len(uni_entries))


class TestCategorizeEntries(TestCase):
    def test_splits_modules_and_rules(self):
        entries = [
            "modules/core/foo.md",
            "rules/bar.md",
            "modules/ci/baz.md",
        ]
        modules, rules = airuleset.categorize_entries(entries)
        self.assertEqual(modules, ["modules/core/foo.md", "modules/ci/baz.md"])
        self.assertEqual(rules, ["rules/bar.md"])


class TestGenerateClaudeMd(TestCase):
    def test_contains_marker(self):
        content = airuleset.generate_claude_md(["modules/core/pr-merge-policy.md"])
        self.assertIn(airuleset.MANAGED_MARKER, content)

    def test_contains_imports(self):
        modules = ["modules/core/pr-merge-policy.md", "modules/ci/test-strictness.md"]
        content = airuleset.generate_claude_md(modules)
        self.assertIn("@~/devel/airuleset/modules/core/pr-merge-policy.md", content)
        self.assertIn("@~/devel/airuleset/modules/ci/test-strictness.md", content)


class TestMergeHooks(TestCase):
    def test_merge_into_empty(self):
        hooks = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "test"}]}]}}
        result = airuleset.merge_hooks_into_settings(hooks, {})
        self.assertIn("hooks", result)
        self.assertIn("SessionStart", result["hooks"])

    def test_preserves_existing_settings(self):
        hooks = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "test"}]}]}}
        existing = {"foo": "bar", "enabledPlugins": {"x": True}}
        result = airuleset.merge_hooks_into_settings(hooks, existing)
        self.assertEqual(result["foo"], "bar")
        self.assertEqual(result["enabledPlugins"], {"x": True})

    def test_no_duplicate_hooks(self):
        hooks = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "test"}]}]}}
        existing = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "test"}]}]}}
        result = airuleset.merge_hooks_into_settings(hooks, existing)
        self.assertEqual(len(result["hooks"]["SessionStart"]), 1)


class TestSkillsExist(TestCase):
    def test_all_skills_have_skill_md(self):
        for skill in airuleset.SKILL_NAMES:
            path = airuleset.REPO_DIR / "skills" / skill / "SKILL.md"
            self.assertTrue(path.exists(), f"Missing SKILL.md: {path}")

    def test_architecture_check_is_user_invocable(self):
        path = airuleset.REPO_DIR / "skills" / "architecture-check" / "SKILL.md"
        content = path.read_text()
        # Keys MUST live in the YAML frontmatter block (between the first two '---'
        # fences), not merely somewhere in the prose body.
        self.assertTrue(content.startswith("---"), "SKILL.md missing frontmatter fence")
        frontmatter = content.split("---", 2)[1]
        self.assertIn("user-invocable: true", frontmatter)
        self.assertIn("disable-model-invocation: true", frontmatter)

    def test_autopilot_is_user_invocable(self):
        path = airuleset.REPO_DIR / "skills" / "autopilot" / "SKILL.md"
        content = path.read_text()
        self.assertTrue(content.startswith("---"), "SKILL.md missing frontmatter fence")
        frontmatter = content.split("---", 2)[1]
        self.assertIn("user-invocable: true", frontmatter)
        self.assertIn("disable-model-invocation: true", frontmatter)


class TestHookScriptsExist(TestCase):
    def test_hook_scripts_exist(self):
        for script in [
            "session-start-fetch.sh",
            "block-sensitive-staging.sh",
            "pre-deploy-clean-tree.sh",
            "stop-check-untracked-work.sh",
            "stop-check-status-marker.sh",
        ]:
            path = airuleset.REPO_DIR / "hooks" / script
            self.assertTrue(path.exists(), f"Missing hook: {path}")
            self.assertTrue(os.access(path, os.X_OK), f"Not executable: {path}")


class TestPreDeployCleanTreeHook(TestCase):
    """Behavioral tests for the dirty-tree deploy guard.

    The incident this guards against: an uncommitted edit rsync'd straight to
    production. The hook is conservative / fail-closed — ANY rsync/scp/sftp/
    sshpass command naming a remote endpoint blocks while the tree is dirty
    (it does not try to prove push vs pull, since that parse fails open). These
    tests lock both the blocks and the deliberate allow cases.
    """

    HOOK = airuleset.REPO_DIR / "hooks" / "pre-deploy-clean-tree.sh"

    def _mkdtemp(self):
        import shutil

        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _run(self, repo: Path, command: str, env_extra=None):
        import subprocess

        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(
            ["bash", str(self.HOOK)],
            input=payload,
            text=True,
            capture_output=True,
            cwd=str(repo),
            env=env,
        )

    def _git(self, repo: Path, *args):
        import subprocess

        subprocess.run(
            ["git", *args],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        )

    def _make_repo(self):
        repo = self._mkdtemp()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.t")
        self._git(repo, "config", "user.name", "t")
        (repo / "app.py").write_text("print('v1')\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "init")
        return repo

    def _dirty_repo(self):
        repo = self._make_repo()
        (repo / "app.py").write_text("print('STRAY REVERT')\n")  # uncommitted edit
        return repo

    # --- clean tree: nothing to protect, everything allowed ---

    def test_clean_tree_push_allowed(self):
        repo = self._make_repo()
        r = self._run(repo, "rsync -a ./ user@host:/srv/app/")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_clean_tree_pull_allowed(self):
        repo = self._make_repo()
        r = self._run(repo, "scp user@host:/etc/config ./local-config")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- dirty tree: remote transfers blocked ---

    def test_dirty_tree_push_blocked(self):
        repo = self._dirty_repo()
        r = self._run(repo, "rsync -a ./ user@host:/srv/app/")
        self.assertEqual(r.returncode, 2, "dirty rsync push must be blocked")
        self.assertIn("BLOCKED", r.stderr)
        self.assertIn("app.py", r.stderr)

    def test_dirty_tree_scp_push_blocked(self):
        repo = self._dirty_repo()
        r = self._run(repo, "sshpass -p x scp app.py newlevel@10.77.9.61:/usr/local/bin/")
        self.assertEqual(r.returncode, 2, "dirty sshpass scp push must be blocked")

    def test_dirty_chained_rsync_push_blocked(self):
        repo = self._dirty_repo()
        r = self._run(
            repo,
            "rsync -a ./ user@host:/srv/ && ssh user@host 'systemctl restart app'",
        )
        self.assertEqual(r.returncode, 2, "dirty push in a chain must still block")

    def test_dirty_rsync_daemon_url_push_blocked(self):
        repo = self._dirty_repo()
        r = self._run(repo, "rsync -a ./ rsync://host/module/")
        self.assertEqual(r.returncode, 2, "dirty rsync:// push must block")

    def test_dirty_pull_conservatively_blocked(self):
        # Fail-closed: a pull while dirty is blocked too (the safe direction).
        repo = self._dirty_repo()
        r = self._run(repo, "scp user@host:/etc/config ./local-config")
        self.assertEqual(r.returncode, 2, "dirty pull is conservatively blocked")

    # --- regression: shell-syntax variants that previously flipped push->pull
    #     (fail-open) must now block. See /review findings. ---

    def test_dirty_push_with_redirect_blocked(self):
        repo = self._dirty_repo()
        r = self._run(repo, "rsync -a ./ user@host:/srv/ 2>&1")
        self.assertEqual(r.returncode, 2, "trailing redirect must not bypass the guard")

    def test_dirty_push_with_trailing_comment_blocked(self):
        repo = self._dirty_repo()
        r = self._run(repo, "scp app.py user@host:/srv/app.py # deploy")
        self.assertEqual(r.returncode, 2, "trailing comment must not bypass the guard")

    def test_dirty_push_flag_value_after_dest_blocked(self):
        repo = self._dirty_repo()
        r = self._run(repo, "scp app.py user@host:/srv/ -P 22")
        self.assertEqual(r.returncode, 2, "option value after dest must not bypass")

    def test_dirty_sudo_rsync_blocked(self):
        repo = self._dirty_repo()
        r = self._run(repo, "sudo rsync -a ./ user@host:/srv/")
        self.assertEqual(r.returncode, 2, "sudo wrapper must not bypass the guard")

    def test_dirty_env_prefix_rsync_blocked(self):
        repo = self._dirty_repo()
        r = self._run(repo, "RSYNC_RSH=ssh rsync -a ./ user@host:/srv/")
        self.assertEqual(r.returncode, 2, "env-assignment prefix must not bypass")

    def test_dirty_subshell_rsync_blocked(self):
        repo = self._dirty_repo()
        r = self._run(repo, "(rsync -a ./ user@host:/srv/)")
        self.assertEqual(r.returncode, 2, "subshell wrapper must not bypass the guard")

    # --- deliberate allows on a dirty tree ---

    def test_dirty_dry_run_allowed(self):
        repo = self._dirty_repo()
        r = self._run(repo, "rsync --dry-run -a ./ user@host:/srv/")
        self.assertEqual(r.returncode, 0, f"--dry-run transfers nothing: {r.stderr}")

    def test_dirty_local_only_rsync_with_colon_flag_allowed(self):
        # A colon inside a flag value (not a remote endpoint) must not trigger.
        repo = self._dirty_repo()
        r = self._run(repo, "rsync -a --exclude=foo:bar ./ ./backup/")
        self.assertEqual(r.returncode, 0, f"local-only rsync must not block: {r.stderr}")

    def test_dirty_echo_mentioning_scp_allowed(self):
        # The command WORD is echo, not scp — must not block.
        repo = self._dirty_repo()
        r = self._run(repo, 'echo "deploy via scp to host:/srv"')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dirty_tree_non_deploy_allowed(self):
        repo = self._dirty_repo()
        r = self._run(repo, "echo deploying && ls -la")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dirty_single_pipe_non_deploy_allowed(self):
        repo = self._dirty_repo()
        r = self._run(repo, "cat app.py | grep print")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- bypasses ---

    def test_dirty_tree_push_bypass_marker(self):
        repo = self._dirty_repo()
        r = self._run(repo, "rsync -a ./ user@host:/srv/  # airuleset:deploy-dirty-ok")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dirty_tree_push_bypass_env(self):
        repo = self._dirty_repo()
        r = self._run(
            repo,
            "rsync -a ./ user@host:/srv/",
            env_extra={"AIRULESET_ALLOW_DIRTY_DEPLOY": "1"},
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dirty_push_blocked_without_jq(self):
        # If jq is absent, the python3 fallback must still parse the command and
        # block — the guard fails ACTIVE, not open.
        import shutil

        binbox = self._mkdtemp()
        needed = [
            "bash", "cat", "grep", "git", "sed", "awk",
            "head", "basename", "python3", "env", "dirname",
        ]
        for b in needed:
            src = shutil.which(b)
            if src:
                os.symlink(src, binbox / b)
        self.assertIsNone(shutil.which("jq", path=str(binbox)), "test PATH must exclude jq")

        repo = self._dirty_repo()
        r = self._run(
            repo, "rsync -a ./ user@host:/srv/", env_extra={"PATH": str(binbox)}
        )
        self.assertEqual(r.returncode, 2, f"jq-absent fallback must still block: {r.stderr}")


class TestNoDroppedWorkHook(TestCase):
    """Behavioral tests for the session-wide untracked-work Stop guard.

    Enforces no-dropped-work.md: any identified-but-unfinished work must be
    fixed now or filed as a #N issue before stopping. Catches the three loss
    patterns the user reported — decomposition-shedding, dropped review
    findings, and 'pre-existing / known / unrelated' test dismissals — on
    EVERY message, not just completion reports. A block is signalled by a
    {"decision":"block"} JSON object on stdout (returncode 0), like the
    sibling prose-violations hook.
    """

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-untracked-work.sh"
    _counter = 0

    def _sid(self):
        # Unique per call so the per-session retry counter never carries over.
        import shutil

        TestNoDroppedWorkHook._counter += 1
        sid = f"test-ndw-{os.getpid()}-{TestNoDroppedWorkHook._counter}"
        self.addCleanup(
            lambda: shutil.os.path.exists(f"/tmp/airuleset-untracked-work-block-{sid}")
            and os.remove(f"/tmp/airuleset-untracked-work-block-{sid}")
        )
        return sid

    def _run(self, msg, sid=None, env_extra=None):
        import subprocess

        sid = sid or self._sid()
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
        r = subprocess.run(
            ["bash", str(self.HOOK)],
            input=payload,
            text=True,
            capture_output=True,
            env=env,
        )
        return r, sid

    def _blocked(self, r):
        return r.returncode == 0 and '"block"' in r.stdout

    def _clean(self, r):
        return r.returncode == 0 and r.stdout.strip() == ""

    # --- dismissals without a filed #N are blocked ---

    def test_preexisting_dismissal_blocked(self):
        r, _ = self._run("That test was already failing before my change — pre-existing, so I skipped it.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_known_issue_dismissal_blocked(self):
        r, _ = self._run("The console warning is a known issue in the upstream lib, nothing to do here.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_unrelated_dismissal_blocked(self):
        r, _ = self._run("The lint failure is unrelated to my change, so I'm leaving it.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_out_of_scope_dismissal_blocked(self):
        r, _ = self._run("Refactoring the auth module is out of scope for this task, moving on.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_separate_problem_dismissal_blocked(self):
        # "separate problem" used to justify skipping, with the noun between
        # the subject and the phrase (so the 'that is' form doesn't match).
        r, _ = self._run("That failing test is a separate problem, not addressing it here.")
        self.assertTrue(self._blocked(r), r.stdout)

    # --- dismissals WITH proof of filing or in-session fix are allowed ---

    def test_preexisting_with_filed_issue_allowed(self):
        r, _ = self._run("The test was failing before my change (pre-existing). Filed as #42: flaky login test.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_dismissal_with_gh_issue_create_allowed(self):
        r, _ = self._run("Found a pre-existing crash in the parser. Ran: gh issue create --title 'parser crash on empty input'.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_dismissal_with_issue_ref_allowed(self):
        r, _ = self._run("That warning is a known limitation — tracked in issue #87, won't address here.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_dismissal_fixed_now_allowed(self):
        r, _ = self._run("Spotted a pre-existing off-by-one. Fixing it now in this commit rather than leaving it.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_completed_fix_of_known_issue_allowed(self):
        # Reporting a COMPLETED fix of a pre-existing/known problem is good work,
        # not a dismissal — a fix verb must not be mistaken for dropping it.
        r, _ = self._run("Fixed a known race condition in the scheduler while I was in there.")
        self.assertTrue(self._clean(r), r.stdout)

    # --- the key anti-false-escape: a stray PR #N must NOT excuse a dismissal ---

    def test_bare_pr_number_does_not_escape_dismissal(self):
        r, _ = self._run("PR #5 is ready and mergeable. The flaky e2e failure is pre-existing.")
        self.assertTrue(self._blocked(r), "a bare PR #N must not satisfy the issue-filed escape: " + r.stdout)

    # --- decomposition-shedding (leftover sub-work) ---

    def test_leftover_parts_blocked(self):
        r, _ = self._run("I implemented the auth piece. The remaining parts (rate-limiting, audit log) can wait.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_handled_only_part_blocked(self):
        r, _ = self._run("Done — though I handled only part of what you asked; the export feature is left.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_leftover_parts_with_issues_allowed(self):
        r, _ = self._run(
            "Implemented auth. Filed the rest as #12: rate-limiting and #13: audit log."
        )
        self.assertTrue(self._clean(r), r.stdout)

    # --- benign messages must not trip ---

    def test_benign_message_allowed(self):
        r, _ = self._run("Done. Pushed commit a1b2c3d. CI is green and the dashboard shows v1.2.3-dev.4.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_empty_message_allowed(self):
        r, _ = self._run("")
        self.assertTrue(self._clean(r), r.stdout)

    # --- retry cap: stops blocking after MAX_RETRIES to avoid loops ---

    def test_retry_cap_releases_after_three_blocks(self):
        sid = self._sid()
        for _ in range(3):
            r, _ = self._run("This failure is pre-existing, skipping.", sid=sid)
            self.assertTrue(self._blocked(r), r.stdout)
        # 4th attempt: retry budget exhausted → let Stop succeed.
        r, _ = self._run("This failure is pre-existing, skipping.", sid=sid)
        self.assertTrue(self._clean(r), f"hook must release after 3 blocks: {r.stdout}")

    # --- jq absent: this Stop nicety fails open (graceful no-op), unlike the deploy gate ---

    def test_jq_absent_no_op(self):
        import shutil

        binbox = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, binbox, ignore_errors=True)
        for b in ["bash", "cat", "grep", "sed", "head", "env"]:
            src = shutil.which(b)
            if src:
                os.symlink(src, binbox / b)
        self.assertIsNone(shutil.which("jq", path=str(binbox)), "test PATH must exclude jq")
        r, _ = self._run(
            "This failure is pre-existing, skipping.", env_extra={"PATH": str(binbox)}
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "", "jq-absent must be a clean no-op")


class TestStatusMarkerHook(TestCase):
    """Behavioral tests for the message-status-marker Stop guard.

    Enforces message-status-marker.md: every message ends with exactly ONE
    state marker (❓ NEEDS YOU / ⏳ WORKING / ✅ DONE) so the user never has to
    guess whether Claude is asking, working in the background, or done. Blocks
    are signalled by {"decision":"block"} JSON on stdout (returncode 0).
    """

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-status-marker.sh"
    _counter = 0

    def _sid(self):
        TestStatusMarkerHook._counter += 1
        sid = f"test-sm-{os.getpid()}-{TestStatusMarkerHook._counter}"
        self.addCleanup(
            lambda: os.path.exists(f"/tmp/airuleset-status-marker-block-{sid}")
            and os.remove(f"/tmp/airuleset-status-marker-block-{sid}")
        )
        return sid

    def _run(self, msg, sid=None, env_extra=None):
        import subprocess

        sid = sid or self._sid()
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
        return subprocess.run(
            ["bash", str(self.HOOK)],
            input=payload,
            text=True,
            capture_output=True,
            env=env,
        )

    def _blocked(self, r):
        return r.returncode == 0 and '"block"' in r.stdout

    def _clean(self, r):
        return r.returncode == 0 and r.stdout.strip() == ""

    # --- background state must be marked ⏳ ---

    def test_background_without_marker_blocked(self):
        r = self._run("Standing by for the mutation result, then the final report. No merge without your go.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_background_marked_working_allowed(self):
        r = self._run("Pushed the fix.\n\n⏳ WORKING: CI run in progress — I'll report when it lands. Nothing needed from you.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_done_claim_while_background_running_blocked(self):
        # "✅ DONE" but something is still running = the exact mislead.
        r = self._run("Kicked off the build, still running in the background.\n\n✅ DONE: build started.")
        self.assertTrue(self._blocked(r), r.stdout)

    # --- questions must be marked ❓ ---

    def test_trailing_question_without_marker_blocked(self):
        r = self._run("The reset can go to 0dB or the last preset. Which behavior do you want?")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_should_i_without_marker_blocked(self):
        r = self._run("PR is green. Should I merge it to main now?")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_question_marked_needs_you_allowed(self):
        r = self._run("PR #5 is green.\n\n❓ NEEDS YOU: approve merge to main?")
        self.assertTrue(self._clean(r), r.stdout)

    # --- progress/completion claims must carry a marker ---

    def test_completion_claim_without_marker_blocked(self):
        r = self._run("Fixed the auth bug and pushed commit a1b2c3d. CI is green.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_completion_claim_with_done_marker_allowed(self):
        r = self._run("Fixed the auth bug, pushed a1b2c3d, CI green.\n\n✅ DONE: auth bug fixed and verified.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_work_complete_heading_counts_as_done(self):
        # A completion report heading is the ✅ DONE marker.
        r = self._run("## ✅ Work Complete\n\nPushed and deployed. Everything green.")
        self.assertTrue(self._clean(r), r.stdout)

    # --- benign / non-status messages must not trip ---

    def test_plain_explanation_allowed(self):
        r = self._run("React re-renders because a new object reference is created each render. Wrap it in useMemo.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_empty_message_allowed(self):
        r = self._run("")
        self.assertTrue(self._clean(r), r.stdout)

    # --- retry cap releases after MAX_RETRIES ---

    def test_retry_cap_releases_after_three_blocks(self):
        sid = self._sid()
        for _ in range(3):
            r = self._run("Standing by for the CI result.", sid=sid)
            self.assertTrue(self._blocked(r), r.stdout)
        r = self._run("Standing by for the CI result.", sid=sid)
        self.assertTrue(self._clean(r), f"must release after 3 blocks: {r.stdout}")

    # --- jq absent: graceful no-op ---

    def test_jq_absent_no_op(self):
        import shutil

        binbox = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, binbox, ignore_errors=True)
        for b in ["bash", "cat", "grep", "sed", "tr", "tail", "head", "env"]:
            src = shutil.which(b)
            if src:
                os.symlink(src, binbox / b)
        self.assertIsNone(shutil.which("jq", path=str(binbox)), "test PATH must exclude jq")
        r = self._run("Standing by for the CI result.", env_extra={"PATH": str(binbox)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "", "jq-absent must be a clean no-op")


class TestRulesHaveFrontmatter(TestCase):
    def test_all_rules_have_paths_frontmatter(self):
        rules_dir = airuleset.REPO_DIR / "rules"
        for rule_file in rules_dir.glob("*.md"):
            content = rule_file.read_text()
            self.assertTrue(
                content.startswith("---"),
                f"Rule missing frontmatter: {rule_file.name}",
            )
            self.assertIn("paths:", content, f"Rule missing paths: {rule_file.name}")


if __name__ == "__main__":
    main()
