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


class TestAutopilotBatching(TestCase):
    """The /autopilot skill + autopilot-worker must bundle bundle-safe issues
    into ONE worker run / ONE PR / ONE CI cycle (cut long-CI cost), governed by
    the existing bundling gate. Locks the feature so it can't silently regress to
    one-PR-per-issue, and so the board-credit-all-members contract stays stated."""

    def _skill(self):
        return (airuleset.REPO_DIR / "skills" / "autopilot" / "SKILL.md").read_text()

    def _worker(self):
        return (airuleset.REPO_DIR / "agents" / "autopilot-worker.md").read_text()

    def test_skill_references_bundling_gate(self):
        s = self._skill()
        self.assertIn("autonomous-batch-issue-development.md", s)
        # the gate's hard ceilings must be stated so a batch stays reviewable
        self.assertIn("bundling gate", s.lower())
        self.assertIn("≤ 4 issues", s)

    def test_skill_dispatches_one_worker_for_a_batch(self):
        s = self._skill()
        self.assertIn("as ONE bundled PR", s)
        # serial-per-repo invariant must be reaffirmed (batch != parallel workers)
        self.assertIn("serial per repo", s.lower())

    def test_skill_no_longer_says_one_issue_only_weak_line(self):
        # the old weak "2-3 trivially-related small issues MAY share one worker"
        # line must be gone — replaced by the real batch-assembly step
        self.assertNotIn("2-3\n   trivially-related", self._skill())

    def test_worker_accepts_a_batch(self):
        w = self._worker()
        self.assertIn("bundled BATCH", w)
        self.assertIn("Work issues #", w)
        # one PR body closes EVERY member, one push, one CI
        self.assertIn("Closes #<n>", w)
        self.assertIn("push **once**", w)

    def test_worker_starts_a_run_per_member(self):
        # board credits each member only if each has its own run; the worker must
        # start one run per named issue and report phases to all of them
        w = self._worker()
        self.assertIn("start a run for **EACH** named issue", w)
        self.assertIn("for R in $RUNS", w)

    def test_worker_drops_gate_violating_member(self):
        # a member that blows the gate mid-flight is dropped, not allowed to
        # bloat the whole batch
        self.assertIn("DROP it from this PR", self._worker())

    def test_worker_terminalizes_dropped_run(self):
        # a dropped / obsolete member's already-started board run MUST be moved to
        # a terminal phase + removed from $RUNS, else it orphans into a false
        # STALE card (the exact lie batching must not reintroduce)
        w = self._worker()
        self.assertIn("--phase stopped", w)
        self.assertIn("--phase obsolete-closed", w)
        self.assertIn("REMOVE `$RUN_K` from `$RUNS`", w)
        self.assertIn("obsolete_closed:", w)

    def test_skill_verify_scopes_to_surviving_set(self):
        # Step 4 must subtract dropped/obsolete members before asserting closed —
        # a legitimately-dropped member (issue left OPEN) is NOT a verify failure
        s = self._skill()
        self.assertIn("SURVIVING set", s)
        self.assertIn("NOT a verify failure", s)

    def test_skill_resolves_member_verdict_by_repo_issue(self):
        # the supervisor never sees the worker's minted run ids — it must resolve
        # each member's run from durable state (repo+issue), per member. (Match on
        # tokens robust to line-wrapping in the prose.)
        s = self._skill()
        self.assertIn("report --repo <repo>", s)
        self.assertIn('--issue "$N" --review supervisor-verify', s)
        self.assertIn("for N in <surviving members>", s)


class TestAutopilotEndOfRunSweep(TestCase):
    """At completion (backlog empty), /autopilot must reconcile the WHOLE tracker
    — INCLUDING autopilot-skip issues — while context is fresh, closing/rescoping
    any ticket the run overcame (hybrid: hard-overcome auto-close, soft/unclear
    ask). Locks the sweep so stale tickets can't silently survive a run."""

    def _skill(self):
        return (airuleset.REPO_DIR / "skills" / "autopilot" / "SKILL.md").read_text()

    def test_sweep_section_exists(self):
        s = self._skill()
        self.assertIn("End-of-run reconciliation sweep", s)
        # routed from the backlog-empty stop, BEFORE the final report
        self.assertIn("Step 4a", s)

    def test_sweep_includes_skipped_issues(self):
        s = self._skill()
        # the whole point: skips are re-examined too, not filtered out
        self.assertIn("skips INCLUDED", s)
        self.assertIn("do NOT filter\n   out `autopilot-skip` here", s)

    def test_sweep_validates_each_via_ticket_validator(self):
        s = self._skill()
        self.assertIn("Validate EACH remaining open issue", s)
        self.assertIn("ticket-validator", s)

    def test_sweep_hybrid_close_policy(self):
        s = self._skill()
        # hard-overcome auto-closes; partial rescopes; soft/unclear asks the user
        self.assertIn("auto-close** with the validator's evidence", s)
        self.assertIn("Rescope it non-", s)
        self.assertIn("ask the user", s)

    def test_sweep_never_prod_classifies(self):
        # approval-scope: closure driven by overcome evidence, never the subject
        self.assertIn("NEVER prod/hardware-classify** any ticket in this sweep",
                      self._skill())


class TestDiscordNotifyHooks(TestCase):
    """Mobile-app device-notification model: the device is pinged ONLY when the
    last turn ended with ❓ NEEDS YOU (a real question) or ✅ DONE (fully done),
    and only when the user is idle/away. notify-discord-pending.sh (Stop) records
    the pending ❓/✅ payload; notify-discord.sh (idle) sends it. ⏳ WORKING / no
    marker → nothing (kills the old 'PROJECT waiting' spam)."""

    PENDING = airuleset.REPO_DIR / "hooks" / "notify-discord-pending.sh"
    IDLE = airuleset.REPO_DIR / "hooks" / "notify-discord.sh"
    _n = 0

    def _sid(self):
        TestDiscordNotifyHooks._n += 1
        sid = f"test-dn-{os.getpid()}-{TestDiscordNotifyHooks._n}"
        p = f"/tmp/claude-discord-pending-{sid}"
        self.addCleanup(lambda: os.path.exists(p) and os.remove(p))
        return sid, p

    def _stop(self, sid, msg):
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
        subprocess.run(["bash", str(self.PENDING)], input=payload, text=True,
                       capture_output=True)

    def _idle(self, sid, cwd):
        payload = json.dumps({"session_id": sid, "cwd": cwd})
        return subprocess.run(["bash", str(self.IDLE)], input=payload, text=True,
                              capture_output=True,
                              env={**os.environ, "DISCORD_NOTIFY_DRYRUN": "1"})

    def test_question_records_then_idle_sends_slovak(self):
        sid, p = self._sid()
        self._stop(sid, "predošlý text\n\n❓ NEEDS YOU: reset EQ na 0 dB alebo "
                        "posledný preset?")
        self.assertTrue(os.path.exists(p), "❓ did not record a pending payload")
        self.assertIn("reset EQ na 0 dB", open(p).read())
        # idle (cwd with no bg shells) sends it, prefixed with project + emoji
        cwd = tempfile.mkdtemp()
        out = self._idle(sid, cwd).stdout
        self.assertIn("❓", out)
        self.assertIn("reset EQ na 0 dB alebo posledný preset?", out)
        self.assertIn(os.path.basename(cwd), out)  # project name
        self.assertFalse(os.path.exists(p), "pending not consumed after send")

    def test_done_multiline_report_records(self):
        sid, p = self._sid()
        self._stop(sid, "## ✅ Work Complete\n\n**Goal:** ...\n\n"
                        "✅ DONE: nasadené v1.2.3, board zelený")
        self.assertTrue(os.path.exists(p))
        self.assertIn("✅", open(p).read())
        self.assertIn("nasadené v1.2.3", open(p).read())

    def test_working_clears_a_prior_question(self):
        sid, p = self._sid()
        self._stop(sid, "❓ NEEDS YOU: nieco?")
        self.assertTrue(os.path.exists(p))
        # next turn is ⏳ WORKING → pending must be cleared (no stale ping)
        self._stop(sid, "⏳ WORKING: CI beží, hlásim sa keď dobehne")
        self.assertFalse(os.path.exists(p), "⏳ did not clear the stale pending")

    def test_no_marker_clears(self):
        sid, p = self._sid()
        self._stop(sid, "❓ NEEDS YOU: q?")
        self._stop(sid, "len bežná odpoveď bez markera")
        self.assertFalse(os.path.exists(p))

    def test_idle_with_nothing_pending_sends_nothing(self):
        # the core anti-spam guarantee: no pending (⏳/unmarked) → no device line
        sid, _ = self._sid()
        out = self._idle(sid, tempfile.mkdtemp()).stdout
        self.assertEqual(out.strip(), "", "sent something with nothing pending")

    def test_default_auto_report_heading_top_pr_url_last_pings_done(self):
        # the canonical merged+deployed report: ✅ Work Complete heading at TOP,
        # PR/URL last — last-line-only detection would MISS the most important
        # "done" event. Whole-message scan + the explicit ✅ DONE line must catch it.
        sid, p = self._sid()
        self._stop(sid, "## ✅ Work Complete\n\n**What changed:** x\n\n"
                        "https://github.com/o/x/pull/5 — merged abc1234\n\n"
                        "✅ DONE: nasadené v1.2.3, board zelený")
        self.assertTrue(os.path.exists(p), "default-auto report recorded nothing")
        self.assertIn("nasadené v1.2.3", open(p).read())

    def test_report_no_done_line_uses_what_changed_without_label(self):
        sid, p = self._sid()
        self._stop(sid, "## ✅ Work Complete\n\n**What changed:** zjednoduchý fix\n\n"
                        "https://github.com/o/x/pull/9 — merged def")
        body = open(p).read()
        self.assertIn("zjednoduchý fix", body)
        self.assertNotIn("What changed", body)   # label stripped
        self.assertNotIn("*", body)               # no leaked markdown

    def test_done_with_trailing_url_after_marker_pings(self):
        sid, p = self._sid()
        self._stop(sid, "✅ DONE: hotovo\n\nhttp://10.77.9.21:8787/")
        self.assertIn("hotovo", open(p).read())

    def test_question_markdown_form_strips_asterisks(self):
        # completion-report template uses "❓ **Question:** <q>"
        sid, p = self._sid()
        self._stop(sid, "## ✅ Work Complete\n\n❓ **Question:** schváliš merge PR #5?")
        body = open(p).read()
        self.assertTrue(body.startswith("❓"))
        self.assertIn("schváliš merge PR #5?", body)
        self.assertNotIn("*", body)
        self.assertNotIn("Question:", body)

    def test_intermediate_done_with_working_last_line_pings_nothing(self):
        # autopilot loop: "merged #5 ✅ DONE … now ⏳ WORKING on #6" — the ⏳ last
        # line means the loop is still running → NO per-issue device ping
        sid, p = self._sid()
        self._stop(sid, "Mergnuté #5 → v1.2.3.\n\n✅ DONE: #5 hotové\n\n"
                        "⏳ WORKING: pokračujem na #6")
        self.assertFalse(os.path.exists(p), "intermediate ✅+⏳ wrongly recorded a ping")

    def test_question_emoji_present_in_idle_send(self):
        # ❓ must reach the device (it bypasses the bg-shell skip in the hook)
        sid, p = self._sid()
        self._stop(sid, "❓ NEEDS YOU: reset na 0 dB alebo posledný preset?")
        out = self._idle(sid, tempfile.mkdtemp()).stdout
        self.assertIn("❓", out)
        self.assertIn("posledný preset?", out)

    def test_idle_output_is_structured_markdown(self):
        # the device line must be Discord-markdown structured (bold header +
        # blockquote on its own line), not one unreadable run-on
        sid, p = self._sid()
        self._stop(sid, "❓ NEEDS YOU: reset na 0 dB alebo posledný preset?")
        cwd = tempfile.mkdtemp()
        out = self._idle(sid, cwd).stdout
        lines = out.strip().split("\n")
        self.assertEqual(len(lines), 2, f"expected header+blockquote, got: {out!r}")
        self.assertTrue(lines[0].startswith("**❓"), lines[0])  # bold header
        self.assertIn(os.path.basename(cwd), lines[0])          # project in header
        self.assertIn("otázka", lines[0])                        # Slovak status
        self.assertTrue(lines[1].startswith("> "), lines[1])     # blockquote
        self.assertIn("posledný preset?", lines[1])
        # ✅ uses the "hotovo" status
        sid2, _ = self._sid()
        self._stop(sid2, "✅ DONE: nasadené v1.2.3")
        out2 = self._idle(sid2, tempfile.mkdtemp()).stdout
        self.assertTrue(out2.startswith("**✅"))
        self.assertIn("hotovo", out2)
        self.assertIn("> nasadené v1.2.3", out2)

    def test_governance_no_hand_fired_per_merge_ping(self):
        # pr-merge-policy.md must NOT instruct an active per-merge device ping
        # (contradicts the mobile model); milestone-notifications.md must state it
        mn = (airuleset.REPO_DIR / "modules" / "core" / "milestone-notifications.md").read_text()
        pm = (airuleset.REPO_DIR / "modules" / "core" / "pr-merge-policy.md").read_text()
        self.assertIn("Mobile-App Model", mn)
        self.assertIn("do NOT call the discord `reply` tool or `PushNotification`", mn)
        self.assertNotIn("Send the milestone ping", pm)

    def test_governance_final_done_only_discipline(self):
        # the ✅-only-at-full-completion rule must be documented as the
        # ⏳-while-looping discipline (so per-issue ✅ never pings)
        mn = (airuleset.REPO_DIR / "modules" / "core" / "milestone-notifications.md").read_text()
        self.assertIn("⏳", mn)
        self.assertIn("FULL completion", mn)

    def test_pending_hook_is_silent_and_nonblocking(self):
        # a Stop notifier must NOT emit a block decision / any stdout (it shares
        # the Stop pipeline with the gate hooks)
        sid, _ = self._sid()
        r = subprocess.run(
            ["bash", str(self.PENDING)], text=True, capture_output=True,
            input=json.dumps({"session_id": sid,
                              "last_assistant_message": "❓ NEEDS YOU: x?"}))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")
        self.assertNotIn("block", r.stdout)


class TestBashHookStdinContract(TestCase):
    """REGRESSION: Claude Code passes the tool payload as JSON on STDIN. Four
    Bash hooks read only the old `$TOOL_INPUT` env var, which CC no longer sets,
    so they were SILENTLY DEAD (secret gate, lint gate, TDD gate, CI-cleanup all
    no-op — 0 CI cancellations, the recurring push churn). These tests lock every
    Bash hook to read stdin so the contract can never silently break again."""

    @staticmethod
    def _bash_hooks_from_settings():
        """Every hooks/*.sh wired under a matcher=='Bash' PreToolUse/PostToolUse
        entry in settings/hooks.json — derived dynamically so a newly-added Bash
        hook is covered automatically (and can't silently ship reading the dead
        $TOOL_INPUT env var)."""
        cfg = json.loads((airuleset.REPO_DIR / "settings" / "hooks.json").read_text())
        names = []
        for event in ("PreToolUse", "PostToolUse"):
            for entry in cfg.get("hooks", {}).get(event, []):
                if entry.get("matcher") != "Bash":
                    continue
                for h in entry.get("hooks", []):
                    cmd = h.get("command", "")
                    if "airuleset/hooks/" in cmd:
                        names.append(cmd.split("airuleset/hooks/")[-1].strip())
        return names

    def test_settings_has_bash_hooks(self):
        # guard: the dynamic discovery actually finds the Bash hooks
        names = self._bash_hooks_from_settings()
        self.assertGreaterEqual(len(names), 5)
        self.assertIn("post-push-ci-cleanup.sh", names)

    def test_every_bash_hook_reads_stdin(self):
        for name in self._bash_hooks_from_settings():
            src = (airuleset.REPO_DIR / "hooks" / name).read_text()
            self.assertIn("cat", src, f"{name}: must read the payload from stdin")
            self.assertRegex(
                src, r"\$\(cat\b",
                f"{name}: must capture stdin via $(cat …) — reading only "
                f"$TOOL_INPUT is the dead contract that disabled the hook")

    def test_no_hook_relies_solely_on_tool_input_env(self):
        for name in self._bash_hooks_from_settings():
            src = (airuleset.REPO_DIR / "hooks" / name).read_text()
            if "TOOL_INPUT" in src:
                # if it references the env var at all, it must be a FALLBACK after
                # a stdin read — never the sole source
                self.assertRegex(
                    src, r"\$\(cat[\s\S]*TOOL_INPUT",
                    f"{name}: $TOOL_INPUT must be a fallback AFTER a stdin read")


class TestSecretStagingHook(TestCase):
    """block-sensitive-staging.sh — the secret-staging gate, via the live stdin
    contract (the exact path that was dead)."""

    HOOK = airuleset.REPO_DIR / "hooks" / "block-sensitive-staging.sh"

    def _run(self, command, use_env=False):
        import subprocess
        if use_env:
            return subprocess.run(["bash", str(self.HOOK)], input="", text=True,
                                  capture_output=True,
                                  env={**os.environ, "TOOL_INPUT": command})
        payload = json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": command}})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True)

    def test_blocks_credentials_via_stdin(self):
        r = self._run("git add credentials.json")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout)

    def test_blocks_env_file(self):
        self.assertEqual(self._run("git add .env").returncode, 2)

    def test_allows_benign_file(self):
        self.assertEqual(self._run("git add README.md").returncode, 0)

    def test_blocks_pem_key_p12_extensions(self):
        # under-match regression: '*.pem' as a grep pattern is a literal asterisk
        for f in ("server.pem", "config/private.key", "cert.p12"):
            self.assertEqual(self._run(f"git add {f}").returncode, 2, f)

    def test_does_not_overmatch_env_substring(self):
        # over-match regression: '.env' regex dot matched 'environment.ts'
        for f in ("src/environment.ts", "lib/keyboard.ts", ".env.example"):
            self.assertEqual(self._run(f"git add {f}").returncode, 0, f)

    def test_blocks_env_local_but_allows_example(self):
        self.assertEqual(self._run("git add .env.local").returncode, 2)
        self.assertEqual(self._run("git add .env.production").returncode, 2)
        self.assertEqual(self._run("git add config/.env.template").returncode, 0)

    def test_empty_command_key_does_not_scan_json(self):
        # a payload with an empty command must NOT fall back to grepping the JSON
        import subprocess
        payload = json.dumps({"tool_input": {"command": "",
                                             "description": "git add .env note"}})
        r = subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                           capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_tool_input_env_fallback_still_blocks(self):
        # old env contract must still work as a fallback (defensive)
        self.assertEqual(self._run("git add .env", use_env=True).returncode, 2)

    def test_empty_payload_does_not_hang_or_block(self):
        import subprocess
        r = subprocess.run(["bash", str(self.HOOK)], input="", text=True,
                           capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0)


class TestPostPushCiCleanupHook(TestCase):
    """post-push-ci-cleanup.sh — fires on git push (via stdin), cancels only
    SUPERSEDED (ancestor-of-HEAD) runs, never the current push's runs."""

    HOOK = airuleset.REPO_DIR / "hooks" / "post-push-ci-cleanup.sh"

    def _run(self, command, cwd=None):
        import subprocess
        payload = json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": command}})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True, cwd=cwd, timeout=30)

    def test_non_push_command_is_noop(self):
        r = self._run("ls -la")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_mention_of_git_push_in_string_is_noop(self):
        # anchored gate: a command merely MENTIONING 'git push' (grep/echo/commit
        # message) must not trigger the cancel/monitor path
        for cmd in ("history | grep 'git push'",
                    "echo 'remember to git push later'",
                    "git commit -m 'document the git push flow'"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 0, cmd)
            self.assertEqual(r.stdout.strip(), "", cmd)

    def test_push_outside_git_repo_is_safe(self):
        # a git push command outside any repo must not crash (set -euo pipefail)
        r = self._run("git push origin dev", cwd=tempfile.mkdtemp())
        self.assertEqual(r.returncode, 0)

    def test_reads_command_from_stdin_json(self):
        # the matcher must see the command inside the JSON payload, not need env
        src = self.HOOK.read_text()
        self.assertRegex(src, r"\$\(cat\b")
        self.assertIn("merge-base --is-ancestor", src)  # supersede-by-ancestor logic

    def _cancel_fixture(self):
        """Temp git repo (OLD ancestor → HEAD) with a landed-push remote-tip and a
        stub `gh` on PATH returning: an ANCESTOR in_progress run (must cancel), the
        push+pull pair at HEAD (must keep), and a DIVERGED run (must keep). Returns
        (repo, env, head, old)."""
        import subprocess, shutil, stat
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        repo = os.path.join(root, "repo")
        bind = os.path.join(root, "bin")
        os.makedirs(repo); os.makedirs(bind)
        g = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)
        g("init", "-q", "-b", "dev")
        g("config", "user.email", "t@t"); g("config", "user.name", "t")
        open(os.path.join(repo, "f"), "w").write("a\n")
        g("add", "f"); g("commit", "-qm", "a")
        old = g("rev-parse", "HEAD").stdout.strip()
        open(os.path.join(repo, "f"), "a").write("b\n")
        g("commit", "-qam", "b")
        head = g("rev-parse", "HEAD").stdout.strip()
        # simulate a LANDED push: remote-tracking ref == HEAD
        g("update-ref", "refs/remotes/origin/dev", head)
        div = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo,
                             input="z\n", text=True, capture_output=True).stdout.strip()
        cancels = os.path.join(root, "cancels")
        gh = os.path.join(bind, "gh")
        with open(gh, "w") as fh:
            fh.write(f'''#!/usr/bin/env bash
[ "$1 $2" = "repo view" ] && {{ echo '{{"name":"x"}}'; exit 0; }}
if [ "$1 $2" = "run list" ]; then
  echo '[{{"databaseId":111,"status":"in_progress","headSha":"{old}","event":"push"}},{{"databaseId":444,"status":"in_progress","headSha":"{head}","event":"push"}},{{"databaseId":555,"status":"in_progress","headSha":"{head}","event":"pull_request"}},{{"databaseId":777,"status":"in_progress","headSha":"{div}","event":"push"}}]'
  exit 0
fi
[ "$1 $2" = "run cancel" ] && {{ echo "$3" >> "{cancels}"; exit 0; }}
exit 0
''')
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        env = dict(os.environ); env["PATH"] = bind + os.pathsep + env["PATH"]
        return repo, env, cancels, head, old

    def _run_env(self, repo, env, command):
        import subprocess
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True, cwd=repo, env=env, timeout=30)

    def test_cancels_only_ancestor_run(self):
        repo, env, cancels, head, old = self._cancel_fixture()
        r = self._run_env(repo, env, "git push origin dev")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        recorded = open(cancels).read().split() if os.path.exists(cancels) else []
        # ONLY the ancestor run (111) cancelled; HEAD pair (444,555) + diverged (777) kept
        self.assertEqual(recorded, ["111"], f"cancelled set wrong: {recorded}")
        self.assertIn("cancelled 1 superseded", r.stdout)
        # monitor instruction lists BOTH current-HEAD runs
        self.assertIn("444", r.stdout); self.assertIn("555", r.stdout)

    def test_no_cancel_when_push_did_not_land(self):
        repo, env, cancels, head, old = self._cancel_fixture()
        # rewind the remote-tracking ref so HEAD != remote tip (push failed/rejected)
        import subprocess
        subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", old], cwd=repo)
        r = self._run_env(repo, env, "git push origin dev")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(cancels) and open(cancels).read().strip(),
                         "cancelled a run although the push did not land")


class TestPrePushGatesFire(TestCase):
    """pre-push-lint.sh + pre-push-test-check.sh were DEAD ($TOOL_INPUT-only). Lock
    that they FIRE via stdin — a non-push command is a clean no-op, a push payload
    reaches the gate body (not silently skipped by an empty input)."""

    def _run(self, hook, command, cwd):
        import subprocess
        payload = json.dumps({"tool_input": {"command": command}})
        # isolate HOME so pre-push-test-check's audit log ($HOME/devel/airuleset/
        # audits/no-test-skips.log) is written under a temp dir, never the real one
        env = dict(os.environ); env["HOME"] = tempfile.mkdtemp()
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / hook)],
                              input=payload, text=True, capture_output=True,
                              cwd=cwd, timeout=60, env=env)

    def test_test_check_blocks_feature_without_test(self):
        # a feature-code change with no test file must block (exit 2) via stdin
        import subprocess, shutil
        root = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        g = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
        g("init", "-q", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
        open(os.path.join(root, "app.py"), "w").write("def f():\n    return 1\n")
        g("add", "app.py"); g("commit", "-qm", "base"); g("branch", "-q", "dev"); g("checkout", "-q", "dev")
        g("update-ref", "refs/remotes/origin/main", g("rev-parse", "HEAD").stdout.strip())
        open(os.path.join(root, "feature.py"), "w").write("def g():\n    return 2\n")
        g("add", "feature.py"); g("commit", "-qm", "feat: add g")
        r = self._run("pre-push-test-check.sh", "git push origin dev", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("BLOCKED", r.stdout)

    def test_test_check_no_test_bypass(self):
        import subprocess, shutil
        root = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        g = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
        g("init", "-q", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
        open(os.path.join(root, "app.py"), "w").write("x=1\n")
        g("add", "app.py"); g("commit", "-qm", "base"); g("branch", "-q", "dev"); g("checkout", "-q", "dev")
        g("update-ref", "refs/remotes/origin/main", g("rev-parse", "HEAD").stdout.strip())
        open(os.path.join(root, "feature.py"), "w").write("y=2\n")
        g("add", "feature.py"); g("commit", "-qm", "config tweak\n\n[no-test: config-only change]")
        r = self._run("pre-push-test-check.sh", "git push origin dev", root)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_non_push_is_noop_for_both_gates(self):
        d = tempfile.mkdtemp()
        for hook in ("pre-push-lint.sh", "pre-push-test-check.sh"):
            self.assertEqual(self._run(hook, "ls -la", d).returncode, 0, hook)


class TestPrePushBaseSyncHook(TestCase):
    """pre-push-base-sync.sh — GLOBAL conflict-churn guard. Blocks a push ONLY when
    a trial merge of the base into HEAD has a REAL CONFLICT (git merge-tree). It
    must NOT block on a mere "behind" (the merge-commit-only divergence after a
    --no-ff PR merge + version bump — the steady-state two-branch push) nor on
    non-push commands that merely mention 'git push'."""

    HOOK = airuleset.REPO_DIR / "hooks" / "pre-push-base-sync.sh"

    def _g(self, cwd, *args):
        import subprocess
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

    def _base_repo(self):
        """Remote + clone, main+dev, with a 3-line 'shared' file (so divergent
        edits to the same line conflict). dev checked out, origin/HEAD set."""
        import shutil
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        bare = os.path.join(root, "rem.git")
        self._g(root, "init", "-q", "--bare", bare)
        repo = os.path.join(root, "repo")
        self._g(root, "clone", "-q", bare, repo)
        self._g(repo, "config", "user.email", "t@t")
        self._g(repo, "config", "user.name", "t")
        self._g(repo, "symbolic-ref", "HEAD", "refs/heads/main")
        open(os.path.join(repo, "shared"), "w").write("line1\nline2\nline3\n")
        self._g(repo, "add", "shared"); self._g(repo, "commit", "-qm", "base")
        self._g(repo, "push", "-q", "origin", "main")
        self._g(repo, "checkout", "-q", "-b", "dev")
        self._g(repo, "push", "-q", "origin", "dev")
        self._g(repo, "remote", "set-head", "origin", "-a")
        return repo

    def _edit_line2(self, repo, branch, text):
        import subprocess
        self._g(repo, "checkout", "-q", branch)
        p = os.path.join(repo, "shared")
        open(p, "w").write(f"line1\n{text}\nline3\n")
        self._g(repo, "commit", "-qam", f"{branch} edit")

    def _run(self, repo, command, env_extra=None):
        import subprocess
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True, cwd=repo, env=env, timeout=30)

    def test_blocks_on_genuine_conflict(self):
        repo = self._base_repo()
        self._edit_line2(repo, "main", "MAIN-EDIT")
        self._g(repo, "push", "-q", "origin", "main")
        self._edit_line2(repo, "dev", "DEV-EDIT")
        r = self._run(repo, "git push origin dev")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("CONFLICT", r.stdout)

    def test_allows_merge_commit_only_behind(self):
        # THE #1 critical false-block: after a --no-ff PR merge + version bump, dev
        # is "behind" main by the merge-commit object but has NO content to merge.
        repo = self._base_repo()
        open(os.path.join(repo, "d"), "w").write("devwork\n")
        self._g(repo, "add", "d"); self._g(repo, "commit", "-qm", "devwork")
        self._g(repo, "push", "-q", "origin", "dev")
        self._g(repo, "checkout", "-q", "main")
        self._g(repo, "merge", "-q", "--no-ff", "dev", "-m", "Merge PR")
        self._g(repo, "push", "-q", "origin", "main")
        self._g(repo, "checkout", "-q", "dev")
        open(os.path.join(repo, "version"), "w").write("v2\n")
        self._g(repo, "add", "version"); self._g(repo, "commit", "-qm", "bump")
        self._g(repo, "remote", "set-head", "origin", "-a")
        r = self._run(repo, "git push origin dev")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_clean_behind(self):
        # main adds a NEW file dev lacks — behind but a clean merge, no conflict
        repo = self._base_repo()
        self._g(repo, "checkout", "-q", "main")
        open(os.path.join(repo, "newfile"), "w").write("x\n")
        self._g(repo, "add", "newfile"); self._g(repo, "commit", "-qm", "newfile")
        self._g(repo, "push", "-q", "origin", "main")
        self._g(repo, "checkout", "-q", "dev")
        open(os.path.join(repo, "dd"), "w").write("y\n")
        self._g(repo, "add", "dd"); self._g(repo, "commit", "-qm", "dwork")
        self._g(repo, "remote", "set-head", "origin", "-a")
        r = self._run(repo, "git push origin dev")
        self.assertEqual(r.returncode, 0, r.stdout)

    def _conflicting(self):
        repo = self._base_repo()
        self._edit_line2(repo, "main", "MAIN-EDIT")
        self._g(repo, "push", "-q", "origin", "main")
        self._edit_line2(repo, "dev", "DEV-EDIT")
        return repo

    def test_non_push_command_not_blocked(self):
        # over-broad-substring regression: these merely MENTION 'git push'
        repo = self._conflicting()
        for cmd in ("grep -rn 'git push' .",
                    "git commit -m 'document the git push flow'",
                    "echo 'remember to git push later'"):
            self.assertEqual(self._run(repo, cmd).returncode, 0, cmd)

    def test_deletion_and_tag_push_allowed(self):
        repo = self._conflicting()
        self.assertEqual(self._run(repo, "git push origin --delete old").returncode, 0)
        self.assertEqual(self._run(repo, "git push origin --tags").returncode, 0)

    def test_pushing_base_branch_allowed(self):
        repo = self._conflicting()
        self.assertEqual(self._run(repo, "git push origin main").returncode, 0)
        self.assertEqual(self._run(repo, "git push origin dev:main").returncode, 0)

    def test_base_word_elsewhere_does_not_bypass(self):
        # the base-target bypass must match only the refspec DESTINATION, not the
        # base word anywhere on the line — else the canonical dev->main workflow
        # command and a base-named feature branch silently skip the conflict guard
        repo = self._conflicting()
        for cmd in ("git push origin dev && gh pr create --base main",
                    "git push -u origin feature-main-fix",
                    "git push origin dev --push-option=ci.skip-main"):
            self.assertEqual(self._run(repo, cmd).returncode, 2, cmd)

    def test_bypasses_allow(self):
        repo = self._conflicting()
        self.assertEqual(self._run(repo, "git push origin dev",
                         env_extra={"AIRULESET_ALLOW_BEHIND_PUSH": "1"}).returncode, 0)
        self.assertEqual(self._run(
            repo, "git push origin dev # airuleset:push-behind-ok").returncode, 0)

    def test_reads_stdin_and_uses_merge_tree(self):
        src = self.HOOK.read_text()
        self.assertRegex(src, r"\$\(cat\b")
        self.assertIn("merge-tree", src)
        self.assertIn("fail-safe", src.lower())


class TestHookScriptsExist(TestCase):
    def test_hook_scripts_exist(self):
        for script in [
            "session-start-fetch.sh",
            "block-sensitive-staging.sh",
            "pre-deploy-clean-tree.sh",
            "stop-check-untracked-work.sh",
            "stop-check-status-marker.sh",
            "stop-check-prod-gating.sh",
            "stop-check-sendmessage-narration.sh",
            "autopilot-report.sh",
            "notify-discord-pending.sh",
            "notify-discord.sh",
            "pre-push-base-sync.sh",
            "post-push-ci-cleanup.sh",
            "pre-push-lint.sh",
            "pre-push-test-check.sh",
        ]:
            path = airuleset.REPO_DIR / "hooks" / script
            self.assertTrue(path.exists(), f"Missing hook: {path}")
            self.assertTrue(os.access(path, os.X_OK), f"Not executable: {path}")


class TestProdGatingHook(TestCase):
    """hooks/stop-check-prod-gating.sh — blocks prod-usage/event/off-air/hardware
    gating (approval-scope.md, the user's hardest rule), in English AND Slovak,
    while letting rule-discussion and plain work reports through."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prod-gating.sh"
    _counter = 0

    def _sid(self):
        TestProdGatingHook._counter += 1
        sid = f"test-pg-{os.getpid()}-{TestProdGatingHook._counter}"
        self.addCleanup(
            lambda: os.path.exists(f"/tmp/airuleset-prod-gating-block-{sid}")
            and os.remove(f"/tmp/airuleset-prod-gating-block-{sid}"))
        return sid

    def _run(self, msg):
        import subprocess
        payload = json.dumps({"last_assistant_message": msg, "session_id": self._sid()})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True)

    def _blocked(self, r):
        return r.returncode == 0 and '"block"' in r.stdout

    def _clean(self, r):
        return r.returncode == 0 and r.stdout.strip() == ""

    def test_slovak_gating_blocked(self):
        r = self._run("väčšina vyžaduje FYZICKÝ rig + off-air okná. Odporúčam "
                      "autopilot-skip na #79 a #81, spraviť ich vedene so mnou, "
                      "nie naslepo. Pri hardvérových issue musíš byť pri tom.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_english_recommend_skip_blocked(self):
        r = self._run("#79 touches a live HDMI output — invasive. I recommend "
                      "autopilot-skip for #79 and #81.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_off_air_window_blocked(self):
        r = self._run("This needs an off-air window — should I wait until the "
                      "stream is off-air?")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_be_present_blocked(self):
        r = self._run("For #84 you must be present at the rig — it needs a "
                      "physical rig and off-air time.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_ask_prod_live_blocked(self):
        r = self._run("Before I deploy to the church stream — is prod live right "
                      "now? Want me to hold until after the event?")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_work_report_allowed(self):
        r = self._run("Worked #79 (DRM master grab on cam2): implemented, tested "
                      "on the rig, all green. Restarted the camera app to verify.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_rule_discussion_allowed(self):
        # meta / prohibition (e.g. this very fix) must NOT be blocked
        r = self._run("Per approval-scope.md the rule now bans off-air gating and "
                      "you must never recommend autopilot-skip; the user guards "
                      "prod-timing.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_plain_status_allowed(self):
        r = self._run("Pushed abc1234. CI green. Deployed v1.2.3 to prod and "
                      "verified the dashboard version.")
        self.assertTrue(self._clean(r), r.stdout)


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

    def test_report_resolves_run_from_repo_issue(self):
        # No --run: resolve the run_id from --repo/--issue (the worker's $RUN
        # shell var does not survive across separate `report` invocations).
        import unittest.mock as m
        from board import reporter
        args = m.Mock(start=False, queue=False, selftest=False, heartbeat=False,
                      run=None, repo="o/x", issue=5, phase="CI", goal=None,
                      approach=None, result=None, note=None, pr=None, review=None)
        with m.patch.object(reporter, "current_run", return_value="o_x-5-1-aa") as cr:
            with m.patch.object(reporter, "report") as rep:
                airuleset.cmd_report(args)
        cr.assert_called_once_with("o/x", 5)
        self.assertEqual(rep.call_args.args[0], "o_x-5-1-aa")
        # repo/issue carried on the event so the board row is never NULL
        self.assertEqual(rep.call_args.kwargs.get("repo"), "o/x")
        self.assertEqual(rep.call_args.kwargs.get("issue"), 5)

    def test_report_recovers_repo_issue_from_run_id(self):
        # --run given but no --repo/--issue: recover them from the persisted map
        # so the mid-run event carries repo/issue (no NULL/unjoinable row).
        import unittest.mock as m
        from board import reporter
        args = m.Mock(start=False, queue=False, selftest=False, heartbeat=False,
                      run="o_x-9-1-bb", repo=None, issue=None, phase="merge",
                      goal=None, approach=None, result=None, note=None, pr=None,
                      review=None)
        with m.patch.object(reporter, "run_to_repo_issue", return_value=("o/x", 9)):
            with m.patch.object(reporter, "report") as rep:
                airuleset.cmd_report(args)
        self.assertEqual(rep.call_args.kwargs.get("repo"), "o/x")
        self.assertEqual(rep.call_args.kwargs.get("issue"), 9)

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
        # keep PATH so python3 + the repo resolve. ISOLATE HOME to a temp dir so
        # the hook's reporter writes its seq/offline-queue under <tmp>/.claude —
        # NEVER the real ~/.claude. Otherwise the queued test event later flushes
        # to the PRODUCTION board (the o_x-1-99-abcd pollution).
        env = dict(os.environ)
        env.pop("AUTOPILOT_RUN", None)
        env.pop("AUTOPILOT_PHASE", None)
        env["HOME"] = tempfile.mkdtemp()
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


class TestSendMessageNarrationHook(TestCase):
    """hooks/stop-check-sendmessage-narration.sh — blocks the "SendMessage isn't
    available here, so I'll dispatch a fresh worker" narration (subagent-
    continuation.md), while letting rule-discussion and normal dispatches pass."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-sendmessage-narration.sh"
    _n = 0

    def _sid(self):
        TestSendMessageNarrationHook._n += 1
        sid = f"test-smn-{os.getpid()}-{TestSendMessageNarrationHook._n}"
        self.addCleanup(
            lambda: os.path.exists(f"/tmp/airuleset-sendmessage-narration-block-{sid}")
            and os.remove(f"/tmp/airuleset-sendmessage-narration-block-{sid}"))
        return sid

    def _run(self, msg):
        payload = json.dumps({"last_assistant_message": msg, "session_id": self._sid()})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True)

    def _blocked(self, r):
        return r.returncode == 0 and '"block"' in r.stdout

    def _clean(self, r):
        return r.returncode == 0 and r.stdout.strip() == ""

    def test_exact_user_phrasing_blocked(self):
        r = self._run("(SendMessage to that worker isn't available here, so I'm "
                      "dispatching a fresh worker to execute the decision, with the "
                      "finding embedded and the restreamer OBS skill enforced.)")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_short_form_blocked(self):
        r = self._run("SendMessage isn't available here, dispatching a fresh worker.")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_rule_discussion_allowed(self):
        r = self._run("Per subagent-continuation.md, never narrate that SendMessage "
                      "is unavailable — just dispatch the fresh worker silently.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_explaining_why_allowed(self):
        r = self._run("Why isn't SendMessage available? It is gated behind "
                      "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS — a known CC limitation.")
        self.assertTrue(self._clean(r), r.stdout)

    def test_normal_dispatch_allowed(self):
        r = self._run("Dispatched the worker for issue #42 with the OBS skill "
                      "enforced. CI green, merged. Done.")
        self.assertTrue(self._clean(r), r.stdout)


class TestManagedSettingsDefaults(TestCase):
    """apply_managed_settings_defaults sets the persistent effortLevel=xhigh default
    in every managed project, preserving all other settings keys, idempotently."""

    def test_sets_effort_xhigh(self):
        out = airuleset.apply_managed_settings_defaults({})
        self.assertEqual(out["effortLevel"], "xhigh")

    def test_preserves_other_keys(self):
        out = airuleset.apply_managed_settings_defaults(
            {"model": "opus", "hooks": {"Stop": []}, "enabledPlugins": {"x": True}})
        self.assertEqual(out["model"], "opus")
        self.assertEqual(out["hooks"], {"Stop": []})
        self.assertEqual(out["enabledPlugins"], {"x": True})
        self.assertEqual(out["effortLevel"], "xhigh")

    def test_idempotent_and_overrides_lower(self):
        once = airuleset.apply_managed_settings_defaults({"effortLevel": "high"})
        twice = airuleset.apply_managed_settings_defaults(once)
        self.assertEqual(once, twice)
        self.assertEqual(twice["effortLevel"], "xhigh")  # raises a lower default

    def test_does_not_mutate_input(self):
        src = {"model": "opus"}
        airuleset.apply_managed_settings_defaults(src)
        self.assertNotIn("effortLevel", src)  # input untouched


class TestUltracodeLauncher(TestCase):
    """apply_ultracode_launcher manages the ~/.bashrc block that launches claude
    in ultracode every shell — idempotent, append-or-replace, never clobbers."""

    def _tmp(self, content=None):
        from pathlib import Path
        d = tempfile.mkdtemp()
        p = Path(d) / ".bashrc"
        if content is not None:
            p.write_text(content)
        return p

    def test_appends_to_existing_bashrc_preserving_content(self):
        p = self._tmp("export PATH=$PATH:/x\nalias ll='ls -la'\n")
        changed = airuleset.apply_ultracode_launcher(p)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn("export PATH=$PATH:/x", text)           # preserved
        self.assertIn("alias ll='ls -la'", text)              # preserved
        self.assertIn("--settings '{\"ultracode\":true}'", text)
        self.assertIn(airuleset.ULTRACODE_MARK_START, text)
        self.assertIn(airuleset.ULTRACODE_MARK_END, text)

    def test_idempotent_no_change_second_run(self):
        p = self._tmp("# my rc\n")
        self.assertTrue(airuleset.apply_ultracode_launcher(p))
        self.assertFalse(airuleset.apply_ultracode_launcher(p))   # second run no-op

    def test_replaces_block_in_place_no_duplicate(self):
        p = self._tmp("# rc\n")
        airuleset.apply_ultracode_launcher(p)
        # tamper inside the block, re-run -> block restored, exactly ONE block
        text = p.read_text().replace('--settings \'{"ultracode":true}\'', "BROKEN")
        p.write_text(text)
        airuleset.apply_ultracode_launcher(p)
        out = p.read_text()
        self.assertEqual(out.count(airuleset.ULTRACODE_MARK_START), 1)
        self.assertNotIn("BROKEN", out)
        self.assertIn("--settings '{\"ultracode\":true}'", out)

    def test_creates_bashrc_when_absent(self):
        from pathlib import Path
        p = Path(tempfile.mkdtemp()) / ".bashrc"
        self.assertTrue(airuleset.apply_ultracode_launcher(p))
        self.assertIn("claude()", p.read_text())

    def test_function_not_alias_and_has_plain_escape(self):
        p = self._tmp()
        airuleset.apply_ultracode_launcher(p)
        text = p.read_text()
        self.assertIn("claude() { command claude", text)   # function, command-prefixed
        self.assertIn("claude-new()", text)                # fresh-session escape hatch
        self.assertIn("claude-plain()", text)              # vanilla escape hatch

    def test_default_launcher_has_skip_perms_and_continue(self):
        p = self._tmp()
        airuleset.apply_ultracode_launcher(p)
        # the `claude()` default carries all three: auto-approve, continue, ultracode
        default_line = next(ln for ln in p.read_text().splitlines()
                            if ln.startswith("claude() {"))
        self.assertIn("--dangerously-skip-permissions", default_line)
        self.assertIn(" -c ", default_line)
        self.assertIn("--settings '{\"ultracode\":true}'", default_line)
        # claude-new is ultracode + skip-perms but NOT -c (fresh session)
        new_line = next(ln for ln in p.read_text().splitlines()
                        if ln.startswith("claude-new() {"))
        self.assertIn("--dangerously-skip-permissions", new_line)
        self.assertNotIn(" -c ", new_line)
