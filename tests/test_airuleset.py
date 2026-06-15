"""Tests for airuleset CLI."""

import json
import os
import subprocess
import sys
import tempfile
import uuid
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


class TestAgentsExist(TestCase):
    def test_agent_names_defined(self):
        self.assertIn("autopilot-worker", airuleset.AGENT_NAMES)

    def test_all_agents_have_md_with_name(self):
        for name in airuleset.AGENT_NAMES:
            path = airuleset.REPO_DIR / "agents" / f"{name}.md"
            self.assertTrue(path.exists(), f"Missing agent: {path}")
            content = path.read_text()
            self.assertTrue(content.startswith("---"), f"{name}.md missing frontmatter")
            frontmatter = content.split("---", 2)[1]
            self.assertIn(f"name: {name}", frontmatter)


class TestHookScriptsExist(TestCase):
    def test_hook_scripts_exist(self):
        for script in [
            "session-start-fetch.sh",
            "block-sensitive-staging.sh",
            "pre-deploy-clean-tree.sh",
            "stop-check-untracked-work.sh",
            "stop-check-status-marker.sh",
            "autopilot-report.sh",
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

    # --- asking permission to file issues (filing is non-destructive, never ask) ---

    def test_ask_permission_to_create_issues_blocked(self):
        # The exact log that frustrated the user.
        r, _ = self._run(
            "Give the word and I'll create the 7 new issues + apply the 4 rescopes "
            "(no code, just the tracked backlog). Or tell me to hold."
        )
        self.assertTrue(self._blocked(r), r.stdout)

    def test_should_i_file_issues_blocked(self):
        r, _ = self._run("I drafted these as a backlog. Should I file these issues or hold off?")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_want_me_to_open_issues_blocked(self):
        r, _ = self._run("Here are the 5 tickets I'd open. Want me to create the issues now?")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_fixed_verb_does_not_excuse_unfiled_backlog(self):
        # The filing-only escape must ignore "fixed" — a fix elsewhere does not
        # mean the proposed backlog was created.
        r, _ = self._run("Fixed the auth bug. Should I create issues for the other 4 ideas, or hold?")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_backlog_already_filed_allowed(self):
        r, _ = self._run("Filed the backlog: #5, #6, #7, #8. Want me to start on #5 now?")
        self.assertTrue(self._clean(r), r.stdout)

    def test_issues_created_report_allowed(self):
        r, _ = self._run("Created issues #12 and #13 for the remaining work via gh issue create.")
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

    # --- a valid terminal marker on the LAST line is authoritative ---
    # Body text that merely MENTIONS a question phrase ("merge it", a body "?") must
    # NOT trip Check B when the message already ends with ✅ DONE / ⏳ WORKING.

    def test_done_marker_with_merge_phrase_in_body_allowed(self):
        # The self-trip case: an answer that explains "merge it" but ends ✅ DONE.
        r = self._run(
            "Add `manual` to stop each PR at green for your \"merge it\".\n"
            "Merge is auto by default.\n\n"
            "✅ DONE: run `/autopilot` (fleet default), then paste the `/loop` line."
        )
        self.assertTrue(self._clean(r), r.stdout)

    def test_done_marker_with_body_question_allowed(self):
        r = self._run(
            "You could ask: which approach? Either works.\n\n"
            "✅ DONE: documented both options."
        )
        self.assertTrue(self._clean(r), r.stdout)

    def test_working_marker_with_question_phrase_in_body_allowed(self):
        r = self._run(
            "Worker will handle merge it and deploy.\n\n"
            "⏳ WORKING: fleet loop running — nothing needed from you."
        )
        self.assertTrue(self._clean(r), r.stdout)

    def test_real_trailing_question_after_done_in_body_still_blocked(self):
        # ✅ DONE is in the BODY, but the LAST line is a real unmarked question —
        # the marker must be the last line, so this is still a violation.
        r = self._run(
            "✅ DONE: part one shipped.\n\n"
            "Should I also deploy to prod now?"
        )
        self.assertTrue(self._blocked(r), r.stdout)

    # --- Check A must not fire on a DESCRIPTIVE mention of a background/polling word ---
    # ("removed the scheduled polling ban") when nothing is running and the message
    # ends ✅ DONE. The genuine "I'm polling CI right now" state claim stays caught.

    def test_descriptive_polling_word_with_done_allowed(self):
        r = self._run(
            "Removed the hard ban on scheduled polling and the /loop restriction.\n\n"
            "✅ DONE: cron/loop tools unblocked."
        )
        self.assertTrue(self._clean(r), r.stdout)

    def test_real_polling_ci_state_still_blocked(self):
        r = self._run("I am still polling CI for the run result.")
        self.assertTrue(self._blocked(r), r.stdout)


class TestSubagentTypeHook(TestCase):
    """pre-agent-validate-subagent-type.sh must allow REAL installed subagents
    (user-level ~/.claude/agents/<name>.md or project .claude/agents/<name>.md),
    not just the hardcoded base types — else a real agent like autopilot-worker
    is wrongly blocked."""

    HOOK = airuleset.REPO_DIR / "hooks" / "pre-agent-validate-subagent-type.sh"

    def _run(self, subagent_type, home=None):
        import subprocess

        env = dict(os.environ)
        if home is not None:
            env["HOME"] = str(home)
        payload = json.dumps({"tool_input": {"subagent_type": subagent_type}})
        return subprocess.run(
            ["bash", str(self.HOOK)], input=payload, text=True, capture_output=True, env=env
        )

    def _tmp_home(self):
        import shutil
        import tempfile

        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / ".claude" / "agents").mkdir(parents=True)
        return home

    def test_base_type_allowed(self):
        self.assertEqual(self._run("general-purpose").returncode, 0)

    def test_hallucinated_blocked(self):
        r = self._run("caveman:cavecrew-builder", home=self._tmp_home())
        self.assertEqual(r.returncode, 2)

    def test_installed_user_agent_allowed(self):
        home = self._tmp_home()
        (home / ".claude" / "agents" / "autopilot-worker.md").write_text(
            "---\nname: autopilot-worker\n---\nbody"
        )
        r = self._run("autopilot-worker", home=home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_uninstalled_agent_blocked(self):
        r = self._run("autopilot-worker", home=self._tmp_home())
        self.assertEqual(r.returncode, 2)


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


class TestProseViolationsAutoMergeSignals(TestCase):
    """Auto-merge-era signals in stop-check-prose-violations.sh."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"

    def _sid(self):
        sid = f"test-prose-{uuid.uuid4().hex[:12]}"
        self.addCleanup(lambda: Path(f"/tmp/airuleset-stop-block-{sid}").unlink(missing_ok=True))
        return sid

    def _run(self, msg, sid=None):
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid or self._sid()})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True)

    def test_merged_prose_report_without_heading_blocked(self):
        msg = ("Merged to main (a1b2c3d), v1.2.0 deployed and verified.\n"
               "https://github.com/zbynekdrlik/foo/pull/12\n"
               "All done for today.")
        r = self._run(msg)
        self.assertEqual(r.returncode, 0)
        self.assertIn('"block"', r.stdout)

    def test_merged_mention_without_pr_url_clean(self):
        r = self._run("Merged to main and deployed v1.2.0 to dev2.\n✅ DONE: shipped")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn('"block"', r.stdout)

    def test_merged_midloop_with_working_marker_clean(self):
        msg = ("Worker finished: merged to main (a1b2c3d), v1.2.0 verified.\n"
               "https://github.com/zbynekdrlik/foo/pull/12\n"
               "Starting issue #13 next.\n"
               "⏳ WORKING: fleet loop continues — nothing needed from you")
        r = self._run(msg)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn('"block"', r.stdout)


class TestIssueRefTitles(TestCase):
    """issue-reference-context.md: every issue/PR ref carries its title — ALL messages.

    The bare-ref check in stop-check-prose-violations.sh is a SOFT warning (stderr,
    no block) — it fires on keyworded bare refs in any message, not just completion
    reports, and stays quiet when a title/topic is present.
    """

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"
    MODULE = airuleset.REPO_DIR / "modules" / "core" / "issue-reference-context.md"

    def _sid(self):
        sid = f"test-ref-{uuid.uuid4().hex[:12]}"
        self.addCleanup(lambda: Path(f"/tmp/airuleset-stop-block-{sid}").unlink(missing_ok=True))
        return sid

    def _run(self, msg):
        payload = json.dumps({"last_assistant_message": msg, "session_id": self._sid()})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True)

    def test_module_exists_and_in_profile(self):
        self.assertTrue(self.MODULE.exists())
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        self.assertIn("modules/core/issue-reference-context.md", entries)

    def test_bare_pr_ref_warns_outside_completion(self):
        # Not a completion report — still warns (the always-on behavior).
        r = self._run("Quick update: PR #7 — pushed and CI is running.\n⏳ WORKING: CI")
        self.assertIn("Bare issue/PR number", r.stderr)

    def test_bare_closes_ref_warns(self):
        r = self._run("Committed the fix. Closes #234 in this commit.\n✅ DONE")
        self.assertIn("Bare issue/PR number", r.stderr)

    def test_bare_issue_mention_warns(self):
        r = self._run("Issue #42 is still open, will pick it up next.\n✅ DONE")
        self.assertIn("Bare issue/PR number", r.stderr)

    def test_titled_pr_ref_clean(self):
        r = self._run("Quick update: PR #7: Refactor driver.rs and add lyrics test — CI running.\n⏳ WORKING: CI")
        self.assertNotIn("Bare issue/PR number", r.stderr)

    def test_titled_closes_ref_clean(self):
        r = self._run("Committed the fix. Closes #234 (driver.rs over the 1000-line cap).\n✅ DONE")
        self.assertNotIn("Bare issue/PR number", r.stderr)

    def test_no_ref_clean(self):
        r = self._run("Pushed the lint fix, nothing else to report.\n✅ DONE")
        self.assertNotIn("Bare issue/PR number", r.stderr)


class TestPreAskAutoAnswerMergeQuestions(TestCase):
    """Merge-permission questions are pre-answered → hook exits 2."""

    HOOK = airuleset.REPO_DIR / "hooks" / "pre-ask-auto-answer.sh"

    def _run(self, question):
        payload = json.dumps({"tool_input": {"questions": [{"question": question}]}})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True)

    def test_merge_permission_question_blocked(self):
        r = self._run("PR #5 is green — should I merge now or wait for your approval?")
        self.assertEqual(r.returncode, 2)
        self.assertIn("BLOCKED", r.stderr)

    def test_want_me_to_merge_blocked(self):
        r = self._run("All gates green. Want me to merge the PR?")
        self.assertEqual(r.returncode, 2)

    def test_design_question_allowed(self):
        r = self._run("Which wording for the reset button label: 'Reset' or 'Clear'?")
        self.assertEqual(r.returncode, 0)

    def test_design_merge_question_allowed(self):
        # "merge" about code design (not a PR) must NOT be blocked
        r = self._run("Should I merge these two config structs into one type?")
        self.assertEqual(r.returncode, 0)


class TestBoardHost(TestCase):
    """Autopilot Board host detection + report/board subcommand registration
    (plan Task 13)."""

    def test_is_board_host_helper_exists_and_returns_bool(self):
        self.assertTrue(hasattr(airuleset, "is_board_host"))
        self.assertIsInstance(airuleset.is_board_host(), bool)

    def test_board_host_ip_constant(self):
        # Default board host IP (env BOARD_HOST may override at runtime).
        self.assertEqual(airuleset.BOARD_HOST_IP, os.getenv("BOARD_HOST", "10.77.9.21"))

    def test_report_and_board_subcommands_registered(self):
        self.assertIn("report", airuleset.SUBCOMMANDS)
        self.assertIn("board", airuleset.SUBCOMMANDS)
        # They must be callables (the dispatch target).
        self.assertTrue(callable(airuleset.SUBCOMMANDS["report"]))
        self.assertTrue(callable(airuleset.SUBCOMMANDS["board"]))

    def test_is_board_host_false_when_ip_not_local(self):
        import unittest.mock as m
        # An IP that is definitely not one of our interfaces → not the board host.
        with m.patch.object(airuleset, "BOARD_HOST_IP", "203.0.113.7"):
            with m.patch.object(airuleset, "_local_ips",
                                return_value={"10.0.0.5", "127.0.0.1"}):
                self.assertFalse(airuleset.is_board_host())

    def test_is_board_host_true_when_ip_local(self):
        import unittest.mock as m
        with m.patch.object(airuleset, "BOARD_HOST_IP", "10.77.9.21"):
            with m.patch.object(airuleset, "_local_ips",
                                return_value={"10.77.9.21", "127.0.1.1"}):
                self.assertTrue(airuleset.is_board_host())


class TestInstallBranch(TestCase):
    """maybe_setup_board() gates setup_board_service() on is_board_host()
    (plan Task 14)."""

    def test_service_setup_skipped_off_board_host(self):
        import unittest.mock as m
        with m.patch.object(airuleset, "is_board_host", return_value=False):
            calls = []
            with m.patch.object(airuleset, "setup_board_service",
                                side_effect=lambda *a, **k: calls.append(1)):
                with m.patch.object(airuleset.Path, "mkdir"):
                    airuleset.maybe_setup_board()
            self.assertEqual(calls, [], "must NOT set up the service off the board host")

    def test_service_setup_runs_on_board_host(self):
        import unittest.mock as m
        with m.patch.object(airuleset, "is_board_host", return_value=True):
            calls = []
            with m.patch.object(airuleset, "setup_board_service",
                                side_effect=lambda *a, **k: calls.append(1)):
                airuleset.maybe_setup_board()
            self.assertEqual(calls, [1], "must set up the service on the board host")


class TestBoardServiceTemplate(TestCase):
    """The systemd --user unit template ships with the required directives
    (plan Task 14)."""

    TEMPLATE = airuleset.REPO_DIR / "settings" / "autopilot-board.service.template"

    def test_template_exists(self):
        self.assertTrue(self.TEMPLATE.exists(), f"Missing: {self.TEMPLATE}")

    def test_template_execstart_and_placeholder(self):
        text = self.TEMPLATE.read_text()
        self.assertIn("{{REPO_DIR}}", text, "must carry the repo-path placeholder")
        self.assertIn("board --serve", text, "ExecStart must run `board --serve`")
        self.assertIn("ExecStart=", text)

    def test_template_hardening_directives(self):
        text = self.TEMPLATE.read_text()
        for directive in [
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "ReadWritePaths=%h/.claude",
            "ProtectHome=read-only",
            "PrivateTmp=yes",
            "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
            "MemoryMax=512M",
            "TasksMax=64",
            "Restart=on-failure",
            "RestartSec=5",
            "StartLimitBurst=5",
        ]:
            self.assertIn(directive, text, f"missing hardening directive: {directive}")

    def test_template_install_section(self):
        text = self.TEMPLATE.read_text()
        self.assertIn("WantedBy=default.target", text)

    def test_render_substitutes_repo_dir(self):
        rendered = airuleset._render_service_unit()
        self.assertNotIn("{{REPO_DIR}}", rendered, "placeholder must be substituted")
        self.assertIn(str(airuleset.REPO_DIR), rendered)


class TestPushRunsTests(TestCase):
    """cmd_push runs the test suite FIRST and aborts the push on failure
    (fail-closed — plan Task 14)."""

    def test_push_aborts_when_tests_fail(self):
        import unittest.mock as m

        # First subprocess.run is the unittest discover — make it fail (rc=1).
        # If the push proceeded, a second subprocess.run (git push) would fire;
        # we assert it does NOT, and that cmd_push raises SystemExit(1).
        test_run = m.Mock(returncode=1)
        runner = m.Mock(side_effect=[test_run])

        with m.patch("subprocess.run", runner):
            with self.assertRaises(SystemExit) as ctx:
                airuleset.cmd_push(m.Mock())

        self.assertEqual(ctx.exception.code, 1)
        # Exactly ONE subprocess.run call (the test run) — no git push attempted.
        self.assertEqual(runner.call_count, 1, "push must not run after failing tests")
        called_args = runner.call_args_list[0].args[0]
        self.assertIn("unittest", called_args, "first call must be the test suite")
        self.assertNotIn("git", called_args)

    def test_push_runs_tests_before_pushing(self):
        import unittest.mock as m

        # Tests pass (rc=0); the git push then fails (rc=1) so cmd_push exits 1
        # BEFORE touching dev2 — but crucially the FIRST call must be the test run.
        test_run = m.Mock(returncode=0, stdout="", stderr="")
        push_run = m.Mock(returncode=1, stdout="", stderr="push rejected")
        runner = m.Mock(side_effect=[test_run, push_run])

        with m.patch("subprocess.run", runner):
            with self.assertRaises(SystemExit):
                airuleset.cmd_push(m.Mock())

        self.assertGreaterEqual(runner.call_count, 2)
        first_call = runner.call_args_list[0].args[0]
        self.assertIn("unittest", first_call, "tests must run before the push")
        second_call = runner.call_args_list[1].args[0]
        self.assertIn("git", second_call, "second call is the git push")


class TestValidateBoard(TestCase):
    """cmd_validate asserts the board modules import + the service template exists
    (plan Task 14)."""

    def test_validate_board_clean(self):
        # On a healthy tree, _validate_board returns no errors.
        self.assertEqual(airuleset._validate_board(), [])

    def test_validate_board_reports_missing_template(self):
        import unittest.mock as m
        fake = airuleset.REPO_DIR / "settings" / "does-not-exist.service.template"
        with m.patch.object(airuleset, "BOARD_SERVICE_TEMPLATE", fake):
            errors = airuleset._validate_board()
        self.assertTrue(any("service template" in e for e in errors), errors)


class TestMonitoredReposDiscovery(TestCase):
    """_monitored_repos() merges BOARD_REPOS env with board.distinct_repos() so
    the refresher activates as soon as any worker reports — no config needed."""

    def test_env_only_when_db_empty(self):
        import unittest.mock as m
        with m.patch.dict(os.environ, {"BOARD_REPOS": "o/a,o/b"}):
            repos = airuleset._monitored_repos()
        self.assertIn("o/a", repos)
        self.assertIn("o/b", repos)

    def test_empty_env_returns_empty_when_no_db(self):
        import unittest.mock as m
        with m.patch.dict(os.environ, {"BOARD_REPOS": ""}, clear=False):
            repos = airuleset._monitored_repos()
        # Without a board DB path we can't assert DB repos, but the call must
        # not raise and must return a list.
        self.assertIsInstance(repos, list)

    def test_discovered_repos_union_env_repos(self):
        """If a run for o/x is in the DB and BOARD_REPOS has o/y, both appear."""
        import unittest.mock as m
        import tempfile
        from board.db import Board
        d = tempfile.mkdtemp()
        db_path = os.path.join(d, "board.sqlite")
        b = Board(db_path)
        b.apply_event({"run_id": "o_x-1-1-abcd", "repo": "o/x", "issue": 1,
                       "seq": 1, "event_id": "e1", "event_ts": 1.0})
        with m.patch.object(airuleset, "BOARD_DB_PATH",
                             airuleset.Path(db_path)):
            with m.patch.dict(os.environ, {"BOARD_REPOS": "o/y"}):
                repos = airuleset._monitored_repos()
        self.assertIn("o/x", repos)
        self.assertIn("o/y", repos)

    def test_invalid_db_repo_filtered_out(self):
        """Repos from the DB that fail valid_repo() are dropped silently."""
        import unittest.mock as m
        import tempfile
        from board.db import Board
        from board import gh as ghmod
        d = tempfile.mkdtemp()
        db_path = os.path.join(d, "board.sqlite")
        b = Board(db_path)
        # Directly insert a bad repo string bypassing apply_event validation.
        c = b.conn()
        c.execute("INSERT INTO runs(run_id, repo, seq) VALUES('r1','bad;repo',0)")
        c.commit()
        c.close()
        with m.patch.object(airuleset, "BOARD_DB_PATH",
                             airuleset.Path(db_path)):
            with m.patch.dict(os.environ, {"BOARD_REPOS": ""}):
                repos = airuleset._monitored_repos()
        self.assertNotIn("bad;repo", repos)

    def test_deduplication(self):
        """A repo in both BOARD_REPOS and the DB appears only once."""
        import unittest.mock as m
        import tempfile
        from board.db import Board
        d = tempfile.mkdtemp()
        db_path = os.path.join(d, "board.sqlite")
        b = Board(db_path)
        b.apply_event({"run_id": "o_x-1-1-aa", "repo": "o/x", "issue": 1,
                       "seq": 1, "event_id": "e1", "event_ts": 1.0})
        with m.patch.object(airuleset, "BOARD_DB_PATH",
                             airuleset.Path(db_path)):
            with m.patch.dict(os.environ, {"BOARD_REPOS": "o/x"}):
                repos = airuleset._monitored_repos()
        self.assertEqual(repos.count("o/x"), 1)


class TestAtomicBoardToken(TestCase):
    """_ensure_board_token creates the file 0600 atomically (O_EXCL), no
    world/group-readable window."""

    def test_creates_token_with_0600_permissions(self):
        import unittest.mock as m
        import tempfile
        d = tempfile.mkdtemp()
        token_path = airuleset.Path(d) / "board_token"
        with m.patch.object(airuleset, "BOARD_TOKEN_PATH", token_path):
            with m.patch.object(airuleset, "CLAUDE_DIR", airuleset.Path(d)):
                tok = airuleset._ensure_board_token()
        self.assertTrue(token_path.exists())
        self.assertEqual(oct(os.stat(token_path).st_mode & 0o777), oct(0o600))
        self.assertTrue(tok)

    def test_reuses_existing_token(self):
        import unittest.mock as m
        import tempfile
        d = tempfile.mkdtemp()
        token_path = airuleset.Path(d) / "board_token"
        # Write a pre-existing token atomically.
        fd = os.open(str(token_path), os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        os.write(fd, b"existing-token-value")
        os.close(fd)
        with m.patch.object(airuleset, "BOARD_TOKEN_PATH", token_path):
            with m.patch.object(airuleset, "CLAUDE_DIR", airuleset.Path(d)):
                tok = airuleset._ensure_board_token()
        self.assertEqual(tok, "existing-token-value")

    def test_race_file_exists_reuses_winner_token(self):
        """FileExistsError from concurrent creation → reuse the existing token.

        Simulates the race by patching os.open to raise FileExistsError on the
        first call (token file didn't exist at the fast-path check but another
        process won the race before our O_EXCL open). The winner's token must
        be returned via the read_text() fallback."""
        import unittest.mock as m
        import tempfile
        d = tempfile.mkdtemp()
        token_path = airuleset.Path(d) / "board_token"
        # Pre-create the winner's token (what the 'winner' process wrote).
        fd = os.open(str(token_path), os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        os.write(fd, b"winner-token")
        os.close(fd)

        real_os_open = os.open
        open_calls = [0]

        def patched_os_open(path, flags, mode=0o777, *a, **kw):
            open_calls[0] += 1
            # First open call targeting our token path: simulate the race loss
            if open_calls[0] == 1 and str(path) == str(token_path):
                raise FileExistsError("simulated race")
            return real_os_open(path, flags, mode, *a, **kw)

        with m.patch.object(airuleset, "BOARD_TOKEN_PATH", token_path):
            with m.patch.object(airuleset, "CLAUDE_DIR", airuleset.Path(d)):
                # fast-path exists() → returns False (file "appears" absent to us)
                with m.patch.object(token_path.__class__, "exists",
                                    lambda self: False
                                    if self == token_path and open_calls[0] == 0
                                    else airuleset.Path.exists(self)):
                    with m.patch("os.open", patched_os_open):
                        tok = airuleset._ensure_board_token()
        self.assertEqual(tok, "winner-token")


class TestReportCommand(TestCase):
    """cmd_report is a thin, crash-proof wrapper over board.reporter (plan Task 13)."""

    def test_report_start_prints_run_id(self):
        import unittest.mock as m
        from board import reporter
        args = m.Mock(start=True, repo="o/x", issue=1, title="t",
                      is_bug_fix=False, has_deploy=False, merge_mode="auto")
        with m.patch.object(reporter, "start_run", return_value="o_x-1-99-abcd") as sr:
            with m.patch("builtins.print") as pr:
                airuleset.cmd_report(args)
        sr.assert_called_once()
        # The run_id is printed to stdout (the worker captures it).
        printed = " ".join(str(c.args[0]) for c in pr.call_args_list if c.args)
        self.assertIn("o_x-1-99-abcd", printed)

    def test_report_phase_routes_to_reporter(self):
        import unittest.mock as m
        from board import reporter
        args = m.Mock(start=False, queue=False, selftest=False, heartbeat=False,
                      run="o_x-1-99-abcd", phase="CI", goal=None, approach=None,
                      result=None, note=None, pr=None, review=None)
        with m.patch.object(reporter, "report") as rep:
            airuleset.cmd_report(args)
        rep.assert_called_once()
        # phase passed through; run_id is the first positional arg.
        self.assertEqual(rep.call_args.args[0], "o_x-1-99-abcd")
        self.assertEqual(rep.call_args.kwargs.get("phase"), "CI")

    def test_report_review_parsing(self):
        # k=v review flags become (check, state) pairs; malformed are dropped.
        pairs = airuleset._parse_reviews(["review=ok", "bad", "plan_check=fail"])
        self.assertIn(("review", "ok"), pairs)
        self.assertIn(("plan_check", "fail"), pairs)
        self.assertNotIn("bad", [p[0] for p in pairs])

    def test_report_never_raises_on_bad_items(self):
        import unittest.mock as m
        args = m.Mock(start=False, queue=True, selftest=False,
                      repo="o/x", items="{not valid json")
        # Must not raise — a malformed --items is reported to stderr, not crashed.
        airuleset.cmd_report(args)


class TestAutopilotReportHook(TestCase):
    """hooks/autopilot-report.sh — a Stop hook that emits a skeleton heartbeat for
    the current autopilot run. It MUST be a pure no-op without AUTOPILOT_RUN, and
    MUST exit 0 even when AUTOPILOT_RUN is set and the board is unreachable
    (fire-and-forget: it backgrounds the report and never blocks the Stop event)."""

    HOOK = airuleset.REPO_DIR / "hooks" / "autopilot-report.sh"

    def _run(self, env_overrides):
        # Start from a clean env (no AUTOPILOT_RUN leaking from the test runner),
        # keep PATH/HOME so python3 + the repo resolve.
        env = dict(os.environ)
        env.pop("AUTOPILOT_RUN", None)
        env.pop("AUTOPILOT_PHASE", None)
        env.update(env_overrides)
        return subprocess.run(
            ["bash", str(self.HOOK)],
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )

    def test_noop_without_autopilot_run(self):
        # No AUTOPILOT_RUN -> exit 0, no output, no report attempted.
        r = self._run({})
        self.assertEqual(r.returncode, 0, f"hook must exit 0 as a no-op: {r.stderr}")
        self.assertEqual(r.stdout.strip(), "")

    def test_exit_zero_with_run_even_when_board_unreachable(self):
        # AUTOPILOT_RUN set + board pointed at an unreachable host -> still exit 0.
        # The report is backgrounded and the reporter swallows the connection error;
        # the Stop hook must never block or fail because of a board outage.
        r = self._run({"AUTOPILOT_RUN": "o_x-1-99-abcd", "BOARD_HOST": "127.0.0.1"})
        self.assertEqual(r.returncode, 0,
                         f"hook must exit 0 with a run set even if board is down: {r.stderr}")

    def test_exit_zero_with_run_and_phase(self):
        # AUTOPILOT_PHASE is optional context; the hook must still exit 0.
        r = self._run({"AUTOPILOT_RUN": "o_x-1-99-abcd",
                       "AUTOPILOT_PHASE": "implementing",
                       "BOARD_HOST": "127.0.0.1"})
        self.assertEqual(r.returncode, 0,
                         f"hook must exit 0 with run+phase set: {r.stderr}")


if __name__ == "__main__":
    main()
