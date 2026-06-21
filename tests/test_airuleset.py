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

    def test_worker_cards_each_merged_member(self):
        # The board is gone — the worker fires the per-ticket Discord card DIRECTLY
        # at merge, one per member (each member's own --issue / --achieved).
        w = self._worker()
        self.assertIn("notify --run-card", w)
        self.assertIn("--repo", w)
        self.assertIn("--issue", w)
        self.assertIn("--achieved", w)
        self.assertIn("PER-TICKET DISCORD CARD", w)

    def test_worker_drops_gate_violating_member(self):
        # a member that blows the gate mid-flight is dropped, not allowed to
        # bloat the whole batch
        self.assertIn("DROP it from this PR", self._worker())

    def test_worker_dropped_member_gets_no_card(self):
        # a dropped / obsolete member is simply not carded (no board run to
        # terminalize); the evidence block still tracks it
        w = self._worker()
        self.assertIn("obsolete_closed:", w)
        self.assertIn("dropped member simply gets no merge card", w)

    def test_skill_verify_scopes_to_surviving_set(self):
        # Step 4 must subtract dropped/obsolete members before asserting closed —
        # a legitimately-dropped member (issue left OPEN) is NOT a verify failure
        s = self._skill()
        self.assertIn("SURVIVING set", s)
        self.assertIn("NOT a verify failure", s)

    def test_skill_card_fired_by_worker_at_merge(self):
        # The board is gone — the per-ticket card is fired by the WORKER directly
        # at merge (notify --run-card), NOT by a board report; the supervisor only
        # confirms each merged member was carded.
        s = self._skill()
        self.assertIn("notify --run-card", s)
        self.assertIn("fired by the WORKER, directly at merge", s)
        self.assertNotIn("supervisor-verify", s)


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
    """Mobile-app device-notification model. ❓ NEEDS YOU is delivered IMMEDIATELY
    by notify-discord-pending.sh (Stop) — the user is blocked on us and the ping
    must reach the phone even over tmux/SSH, where Claude Code's idle_prompt event
    is unreliable. ✅ DONE is recorded and delivered by notify-discord.sh only when
    the user is idle/away (less urgent; no spam per finished turn). Both share the
    single send path notify-discord-send.sh. ⏳ / no marker → nothing."""

    PENDING = airuleset.REPO_DIR / "hooks" / "notify-discord-pending.sh"
    IDLE = airuleset.REPO_DIR / "hooks" / "notify-discord.sh"
    _n = 0

    def _sid(self):
        TestDiscordNotifyHooks._n += 1
        sid = f"test-dn-{os.getpid()}-{TestDiscordNotifyHooks._n}"
        p = f"/tmp/claude-discord-pending-{sid}"
        self.addCleanup(lambda: os.path.exists(p) and os.remove(p))
        return sid, p

    def _stop(self, sid, msg, cwd="", owner="", home=None):
        # Hermetic: DRYRUN + ND_DRYRUN_FILE → the ❓ immediate-send composes to a
        # file (never a real Discord POST, never stdout). _sent() reads that file.
        sf = f"/tmp/claude-dn-send-{sid}"
        self.addCleanup(lambda: os.path.exists(sf) and os.remove(sf))
        if os.path.exists(sf):
            os.remove(sf)
        self._send_file = sf
        env = {**os.environ, "DISCORD_NOTIFY_DRYRUN": "1", "ND_DRYRUN_FILE": sf,
               "AIRULESET_NOTIFY_OWNER": owner}
        if home:
            env["HOME"] = home
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": cwd})
        return subprocess.run(["bash", str(self.PENDING)], input=payload, text=True,
                              capture_output=True, env=env)

    def _sent(self):
        # the device line the Stop hook delivered IMMEDIATELY (❓), or "" if none
        f = getattr(self, "_send_file", "")
        if not (f and os.path.exists(f)):
            return ""
        with open(f) as fh:
            return fh.read()

    def _idle(self, sid, cwd, owner=""):
        # owner="" forces NO @mention so the structure assertions are deterministic
        # regardless of this machine's tmux session / .env mapping. The mention
        # behaviour is covered by test_idle_prepends_owner_mention + the
        # TestDiscordAutopilotNotify unit tests.
        payload = json.dumps({"session_id": sid, "cwd": cwd})
        return subprocess.run(["bash", str(self.IDLE)], input=payload, text=True,
                              capture_output=True,
                              env={**os.environ, "DISCORD_NOTIFY_DRYRUN": "1",
                                   "AIRULESET_NOTIFY_OWNER": owner})

    def test_question_fires_immediately_no_pending(self):
        sid, p = self._sid()
        cwd = tempfile.mkdtemp()
        self._stop(sid, "predošlý text\n\n❓ NEEDS YOU: reset EQ na 0 dB alebo "
                        "posledný preset?", cwd=cwd)
        sent = self._sent()
        self.assertIn("❓", sent)
        self.assertIn("reset EQ na 0 dB alebo posledný preset?", sent)
        self.assertIn(os.path.basename(cwd), sent)  # project name in header
        self.assertFalse(os.path.exists(p),
                         "❓ must NOT leave a pending (it was sent immediately)")
        # idle afterwards has nothing to send (already delivered on Stop)
        out = self._idle(sid, cwd).stdout
        self.assertEqual(out.strip(), "")

    def test_done_multiline_report_records(self):
        sid, p = self._sid()
        self._stop(sid, "## ✅ Work Complete\n\n**Goal:** ...\n\n"
                        "✅ DONE: nasadené v1.2.3, CI zelené")
        self.assertTrue(os.path.exists(p))
        self.assertIn("✅", open(p).read())
        self.assertIn("nasadené v1.2.3", open(p).read())

    def test_working_clears_a_prior_done(self):
        sid, p = self._sid()
        self._stop(sid, "✅ DONE: hotovo")          # ✅ records a pending
        self.assertTrue(os.path.exists(p))
        # next turn is ⏳ WORKING → pending must be cleared (no stale ping)
        self._stop(sid, "⏳ WORKING: CI beží, hlásim sa keď dobehne")
        self.assertFalse(os.path.exists(p), "⏳ did not clear the stale pending")

    def test_no_marker_clears(self):
        sid, p = self._sid()
        self._stop(sid, "✅ DONE: hotovo")          # ✅ records a pending
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
                        "✅ DONE: nasadené v1.2.3, CI zelené")
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
        self._stop(sid, "✅ DONE: hotovo\n\nhttp://100.104.8.125:8787/")
        self.assertIn("hotovo", open(p).read())

    def test_question_markdown_form_strips_asterisks(self):
        # completion-report template uses "❓ **Question:** <q>" — the question TEXT
        # must reach the device with the **Question:** label + asterisks stripped.
        sid, p = self._sid()
        self._stop(sid, "## ✅ Work Complete\n\n❓ **Question:** schváliš merge PR #5?")
        sent = self._sent()
        self.assertIn("❓", sent)
        # the blockquote line carries the cleaned question (header legitimately
        # uses ** bold, so only the > line is asserted asterisk-free)
        qline = [l for l in sent.splitlines() if l.startswith("> ")][0]
        self.assertIn("schváliš merge PR #5?", qline)
        self.assertNotIn("*", qline)
        self.assertNotIn("Question:", qline)
        self.assertFalse(os.path.exists(p), "❓ must not leave a pending")

    def test_intermediate_done_with_working_last_line_pings_nothing(self):
        # autopilot loop: "merged #5 ✅ DONE … now ⏳ WORKING on #6" — the ⏳ last
        # line means the loop is still running → NO per-issue device ping
        sid, p = self._sid()
        self._stop(sid, "Mergnuté #5 → v1.2.3.\n\n✅ DONE: #5 hotové\n\n"
                        "⏳ WORKING: pokračujem na #6")
        self.assertFalse(os.path.exists(p), "intermediate ✅+⏳ wrongly recorded a ping")

    def test_question_emoji_present_in_immediate_send(self):
        # ❓ must reach the device immediately on Stop (no idle dependency)
        sid, p = self._sid()
        self._stop(sid, "❓ NEEDS YOU: reset na 0 dB alebo posledný preset?")
        sent = self._sent()
        self.assertIn("❓", sent)
        self.assertIn("posledný preset?", sent)
        self.assertFalse(os.path.exists(p))

    def test_immediate_question_is_structured_markdown(self):
        # the ❓ device line must be Discord-markdown structured (bold header +
        # blockquote on its own line), not one unreadable run-on
        sid, p = self._sid()
        cwd = tempfile.mkdtemp()
        self._stop(sid, "❓ NEEDS YOU: reset na 0 dB alebo posledný preset?", cwd=cwd)
        sent = self._sent().strip()
        lines = sent.split("\n")
        self.assertEqual(len(lines), 2, f"expected header+blockquote, got: {sent!r}")
        self.assertTrue(lines[0].startswith("**❓"), lines[0])  # bold header
        self.assertIn(os.path.basename(cwd), lines[0])          # project in header
        self.assertIn("otázka", lines[0])                        # Slovak status
        self.assertTrue(lines[1].startswith("> "), lines[1])     # blockquote
        self.assertIn("posledný preset?", lines[1])

    def test_idle_done_is_structured_markdown(self):
        # ✅ still goes through the idle path; same structured markdown
        sid, _ = self._sid()
        self._stop(sid, "✅ DONE: nasadené v1.2.3")
        out2 = self._idle(sid, tempfile.mkdtemp()).stdout
        self.assertTrue(out2.startswith("**✅"))
        self.assertIn("hotovo", out2)
        self.assertIn("> nasadené v1.2.3", out2)

    def _mention_home(self):
        home = tempfile.mkdtemp()
        d = Path(home) / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        (d / ".env").write_text("DISCORD_MENTION_ZBYNEK=773451844110385193\n")
        return home

    def test_immediate_question_prepends_owner_mention(self):
        # The ❓ immediate ping must @mention the tmux owner. Hermetic: temp HOME
        # with a DISCORD_MENTION_ZBYNEK map + forced owner=zbynek.
        sid, _p = self._sid()
        self._stop(sid, "❓ NEEDS YOU: reset na 0 dB?",
                   owner="zbynek", home=self._mention_home())
        self.assertTrue(self._sent().startswith("<@773451844110385193> **❓"),
                        f"❓ ping not @mention-prefixed: {self._sent()!r}")

    def test_idle_prepends_owner_mention(self):
        # The idle ✅ ping must @mention the tmux owner too (same shared sender).
        sid, _p = self._sid()
        home = self._mention_home()
        self._stop(sid, "✅ DONE: nasadené v1.2.3")   # records a pending ✅
        payload = json.dumps({"session_id": sid, "cwd": tempfile.mkdtemp()})
        out = subprocess.run(
            ["bash", str(self.IDLE)], input=payload, text=True, capture_output=True,
            env={**os.environ, "HOME": home, "DISCORD_NOTIFY_DRYRUN": "1",
                 "AIRULESET_NOTIFY_OWNER": "zbynek"}).stdout
        self.assertTrue(out.startswith("<@773451844110385193> **✅"),
                        f"idle ping not @mention-prefixed: {out!r}")

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

    def test_governance_question_pings_immediately(self):
        # ❓ NEEDS YOU is documented as an IMMEDIATE device ping (not idle-gated),
        # because Claude Code's idle_prompt event is unreliable over tmux/SSH
        mn = (airuleset.REPO_DIR / "modules" / "core" / "milestone-notifications.md").read_text()
        self.assertIn("IMMEDIATELY", mn)

    def test_pending_hook_is_silent_and_nonblocking(self):
        # a Stop notifier must NOT emit a block decision / any stdout (it shares
        # the Stop pipeline with the gate hooks) — even when it fires the ❓ send
        # immediately (that send backgrounds its own curl / writes to the dryrun
        # file, never to stdout).
        sid, _ = self._sid()
        r = self._stop(sid, "❓ NEEDS YOU: x?")
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

    def test_fork_allowed(self):
        # `fork` is a built-in (forks the parent) — NOT a file-backed agent, so it
        # must be in the allowlist or a valid fork dispatch is wrongly blocked.
        self.assertEqual(self._run("fork", home=self._tmp_home()).returncode, 0)

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


class TestDiscordAutopilotNotify(TestCase):
    """`airuleset.py notify` — the single Discord send path: tmux-owner @mention,
    the autopilot per-ticket completion card, and dedup."""

    AIRULESET = airuleset.REPO_DIR / "airuleset.py"
    IDLE_HOOK = airuleset.REPO_DIR / "hooks" / "notify-discord.sh"
    SEND_HOOK = airuleset.REPO_DIR / "hooks" / "notify-discord-send.sh"

    def setUp(self):
        import notify
        self.notify = notify

    # --- registration -----------------------------------------------------
    def test_notify_registered(self):
        self.assertIn("notify", airuleset.SUBCOMMANDS)
        self.assertTrue(callable(airuleset.SUBCOMMANDS["notify"]))

    # --- @mention resolution ---------------------------------------------
    def test_mention_prefix_maps_owner_to_id(self):
        env = {"DISCORD_MENTION_ZBYNEK": "111222333",
               "DISCORD_MENTION_MAREK": "444555666"}
        self.assertEqual(self.notify.mention_prefix(env=env, owner="zbynek"),
                         "<@111222333> ")
        self.assertEqual(self.notify.mention_prefix(env=env, owner="marek"),
                         "<@444555666> ")

    def test_mention_prefix_unknown_owner_is_empty(self):
        env = {"DISCORD_MENTION_ZBYNEK": "111"}
        self.assertEqual(self.notify.mention_prefix(env=env, owner="nobody"), "")
        self.assertEqual(self.notify.mention_prefix(env=env, owner=""), "")

    def test_mention_prefix_passes_through_literal_mention(self):
        # A value already shaped like a mention (role / @here) is used verbatim.
        env = {"DISCORD_MENTION_ZBYNEK": "<@&9988>"}
        self.assertEqual(self.notify.mention_prefix(env=env, owner="zbynek"),
                         "<@&9988> ")

    def test_resolve_owner_env_override(self):
        import unittest.mock as m
        with m.patch.dict(os.environ, {"AIRULESET_NOTIFY_OWNER": "Marek-X"}):
            self.assertEqual(self.notify.resolve_owner(), "marekx")

    # --- card composition -------------------------------------------------
    def test_card_has_goal_achieved_review_progress(self):
        card = self.notify.compose_autopilot_card(
            repo="o/cam", pr=88, merge_sha="abc1234", version="v1.4.2",
            review_ok=True, done=3, remaining=5,
            tickets=[{"n": 41, "title": "NDI rebind",
                      "goal": "Kamera padla", "achieved": "Watchdog pridany"}])
        # the two sections the user asked for, per ticket
        self.assertIn("🎯 **Cieľ:** Kamera padla", card)
        self.assertIn("✅ **Dosiahnuté:** Watchdog pridany", card)
        self.assertIn("#41 — NDI rebind", card)
        # double-review verdict
        self.assertIn("Double-review:", card)
        self.assertIn("splnené", card)
        # backlog progress
        self.assertIn("hotové 3 · ostáva 5", card)
        # deploy facts
        self.assertIn("v1.4.2", card)
        self.assertIn("PR #88", card)
        # NO stray separator right after the box emoji
        self.assertNotIn("📦 ·", card)

    def test_card_review_fail_marks_nesplnene(self):
        card = self.notify.compose_autopilot_card(
            repo="o/x", pr=1, tickets=[{"n": 1, "goal": "g", "achieved": "a"}],
            review_ok=False, done=1, remaining=0)
        self.assertIn("NESPLNENÉ", card)
        self.assertIn("❌", card)
        # remaining 0 → backlog-empty flourish
        self.assertIn("backlog prázdny", card)

    def test_card_plural_vs_singular(self):
        one = self.notify.compose_autopilot_card(
            repo="o/x", tickets=[{"n": 1}])
        two = self.notify.compose_autopilot_card(
            repo="o/x", tickets=[{"n": 1}, {"n": 2}])
        self.assertIn("ticket dokončený", one)
        self.assertIn("2 tickety dokončené", two)

    # --- API-error notifier (the CONCRETE stall signal) ------------------
    def test_is_api_error_catches_real_cc_errors(self):
        for t in [
            "API Error: Server is temporarily limiting requests (not your usage limit) · Rate limited",
            "API Error: The socket connection was closed unexpectedly.",
            "There's an issue with the selected model (claude-fable-5). It may not exist...",
            "API Error: Overloaded",
            "Claude usage limit reached. Try again later.",
        ]:
            self.assertTrue(self.notify.is_api_error(t), t)

    def test_is_api_error_rejects_normal_prose(self):
        # the false positives that caused spam — normal work that MENTIONS the words
        for t in [
            "✅ DONE: nasadené v1.2.3",
            "I'll fix the rate limiter config in src/limiter.py and add a test.",
            "The server was overloaded so I added caching to reduce load.",
            "Pridal som rate limit do API endpointu podľa zadania.",
            "⏳ WORKING: monitorujem CI",
            "",
        ]:
            self.assertFalse(self.notify.is_api_error(t), t)

    def test_api_error_alert_uses_real_text(self):
        a = self.notify.compose_api_error_alert(
            "zbynekdrlik/odoo-erp",
            "API Error: Server is temporarily limiting requests · Rate limited")
        self.assertIn("odoo-erp", a)
        self.assertNotIn("zbynekdrlik/odoo-erp", a)   # name only
        self.assertIn("API chyba", a)
        self.assertIn("Rate limited", a)              # the ACTUAL error text

    def test_cli_api_error_sends_only_on_real_error(self):
        # CLI --api-error: a real error → sends; normal prose → nothing.
        home = self._env_home()
        env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": "zbynek"}
        real = subprocess.run(
            [sys.executable, str(self.AIRULESET), "notify", "--api-error",
             "--dry-run", "--project", "odoo-erp", "--session", "s1", "--text",
             "API Error: Server is temporarily limiting requests · Rate limited"],
            capture_output=True, text=True, env=env)
        self.assertIn("<@111222333>", real.stdout)   # _env_home() zbynek id
        self.assertIn("API chyba", real.stdout)
        normal = subprocess.run(
            [sys.executable, str(self.AIRULESET), "notify", "--api-error",
             "--dry-run", "--project", "odoo-erp", "--session", "s1", "--text",
             "I'll fix the rate limiter config and add a test."],
            capture_output=True, text=True, env=env)
        self.assertEqual(normal.stdout.strip(), "")   # not an error → nothing

    def test_api_error_hook_wired_in_stop(self):
        src = (airuleset.REPO_DIR / "settings" / "hooks.json").read_text()
        self.assertIn("notify-api-error.sh", src)
        hook = (airuleset.REPO_DIR / "hooks" / "notify-api-error.sh").read_text()
        self.assertIn("--api-error", hook)
        self.assertIn("last_assistant_message", hook)

    def test_card_header_shows_repo_name_not_owner(self):
        # The @mention already names the person; an "owner/" prefix in the header
        # repeats it ("@Zbynek Drlik … zbynekdrlik/bakerion-ai"). Header = name only.
        card = self.notify.compose_autopilot_card(
            repo="zbynekdrlik/bakerion-ai", tickets=[{"n": 7, "goal": "g",
                                                      "achieved": "a"}])
        self.assertIn("🚀 **bakerion-ai**", card)
        self.assertNotIn("zbynekdrlik/bakerion-ai", card)

    def test_card_progress_remaining_only(self):
        # The merge-triggered run-card knows only `remaining` (not done) → show
        # "ostáva Y", never a bogus "hotové".
        card = self.notify.compose_autopilot_card(
            repo="o/x", tickets=[{"n": 1, "goal": "g", "achieved": "a"}],
            remaining=5)
        self.assertIn("ostáva 5", card)
        self.assertNotIn("hotové", card)

    def test_run_card_gathers_title_and_backlog_then_sends(self):
        # The worker fires `notify --run-card --repo --issue` directly at merge —
        # repo + issue are passed explicitly (no board run_id fallback).
        import unittest.mock as m
        args = m.Mock(run_card=True, autopilot_done=False, mention_prefix=False,
                      body=None, run=None, repo="o/x", issue=5,
                      pr="https://h/pull/9", achieved="did the thing", result=None,
                      version=None, merge_sha=None, review="ok", dedup_key=None,
                      dry_run=False)
        captured = {}

        def fake_gh(*a, **k):
            return "Real Issue Title" if "view" in a else "7"

        def fake_send(body, **k):
            captured["body"] = body
            captured["dedup"] = k.get("dedup_key")
            return "sent"

        with m.patch.object(airuleset, "_gh_out", side_effect=fake_gh):
            with m.patch("notify.send", side_effect=fake_send):
                airuleset.cmd_notify(args)
        b = captured["body"]
        self.assertIn("🎯 **Cieľ:** Real Issue Title", b)
        self.assertIn("✅ **Dosiahnuté:** did the thing", b)
        self.assertIn("PR #9", b)          # extracted from the URL
        self.assertIn("ostáva 7", b)
        # dedup on repo-NAME#issue (stable), NOT the run id
        self.assertEqual(captured["dedup"], "x#5")

    def test_run_card_dedup_survives_redispatch(self):
        # The recurring duplicate bug: /autopilot re-dispatches a fresh worker each
        # turn, so the same issue can be carded twice. Dedup must key on
        # repo-name#issue so the SAME issue is carded once, regardless of bare-vs-full
        # repo form.
        import unittest.mock as m
        keys = []

        def fake_send(body, **k):
            keys.append(k.get("dedup_key"))
            return "sent"

        def mk(repo):
            return m.Mock(run_card=True, autopilot_done=False, mention_prefix=False,
                          body=None, run=None, repo=repo, issue=606, pr=None,
                          achieved="a", result=None, version=None, merge_sha=None,
                          review="ok", dedup_key=None, dry_run=False)

        with m.patch.object(airuleset, "_gh_out",
                            side_effect=lambda *a, **k: "T" if "view" in a else "3"):
            with m.patch("notify.send", side_effect=fake_send):
                airuleset.cmd_notify(mk("zbynekdrlik/odoo-erp"))
                airuleset.cmd_notify(mk("odoo-erp"))  # re-dispatch, bare repo
        self.assertEqual(keys, ["odoo-erp#606", "odoo-erp#606"])  # identical key both times

    def test_send_error_keeps_dedup_claim(self):
        # A POST error must NOT release the claim (a timeout can fire after Discord
        # accepted the message → releasing would duplicate). Retry stays deduped.
        import unittest.mock as m
        with tempfile.TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                env = {"DISCORD_BOT_TOKEN": "x",
                       "DISCORD_NOTIFICATION_CHANNEL_ID": "1"}
                with m.patch("notify.urllib.request.urlopen",
                             side_effect=OSError("boom")):
                    r = self.notify.send("hi", env=env, owner="", dedup_key="k#err")
                self.assertEqual(r, "error")
                # claim kept → a later retry is a dedup hit, not a duplicate send
                self.assertFalse(self.notify._dedup_claim("k#err"))

    def test_card_redacts_secrets(self):
        card = self.notify.compose_autopilot_card(
            repo="o/x", tickets=[{"n": 1, "goal": "token ghp_abcdEFGH1234567890",
                                  "achieved": "ok"}])
        self.assertNotIn("ghp_abcdEFGH1234567890", card)
        self.assertIn("[redacted]", card)

    # --- send: dry-run + dedup -------------------------------------------
    def test_send_dry_run_prepends_mention_and_does_not_claim(self):
        import io, contextlib
        env = {"DISCORD_MENTION_ZBYNEK": "111222333"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r1 = self.notify.send("BODY", env=env, owner="zbynek",
                                  dedup_key="k#1", dry_run=True)
            r2 = self.notify.send("BODY", env=env, owner="zbynek",
                                  dedup_key="k#1", dry_run=True)
        out = buf.getvalue()
        self.assertEqual(r1, "dry-run")
        # dry-run does NOT claim dedup → the second dry-run still prints (re-runnable)
        self.assertEqual(r2, "dry-run")
        self.assertIn("<@111222333> BODY", out)

    def test_dedup_claim_then_release(self):
        import unittest.mock as m
        with tempfile.TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                self.assertTrue(self.notify._dedup_claim("o/x#5"))   # first wins
                self.assertFalse(self.notify._dedup_claim("o/x#5"))  # second blocked
                self.notify._dedup_release("o/x#5")
                self.assertTrue(self.notify._dedup_claim("o/x#5"))   # reclaimable

    def test_send_sets_discordbot_user_agent(self):
        # Cloudflare 403s the default "Python-urllib" UA (error code 1010), so
        # send() MUST set a DiscordBot User-Agent or EVERY card silently fails
        # (caught only by a live POST — this locks the regression).
        import unittest.mock as m
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}

            class _R:
                def read(self):
                    return b""
            return _R()

        env = {"DISCORD_BOT_TOKEN": "x", "DISCORD_NOTIFICATION_CHANNEL_ID": "1"}
        with tempfile.TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                with m.patch("notify.urllib.request.urlopen",
                             side_effect=fake_urlopen):
                    r = self.notify.send("hi", env=env, owner="", dedup_key=None)
        self.assertEqual(r, "sent")
        ua = captured["headers"].get("user-agent", "")
        self.assertIn("DiscordBot", ua)
        self.assertNotIn("Python-urllib", ua)

    def test_send_no_config_releases_dedup(self):
        # A real (non-dry) send with no token must NOT permanently claim the key,
        # so a later configured send can still deliver the card.
        import unittest.mock as m
        with tempfile.TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                r = self.notify.send("BODY", env={}, owner="zbynek",
                                     dedup_key="o/x#9")
                self.assertEqual(r, "no-config")
                # key released → reclaimable
                self.assertTrue(self.notify._dedup_claim("o/x#9"))

    # --- end-to-end CLI ---------------------------------------------------
    def _env_home(self):
        home = tempfile.mkdtemp()
        d = Path(home) / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        (d / ".env").write_text(
            "DISCORD_MENTION_ZBYNEK=111222333\n"
            "DISCORD_MENTION_MAREK=444555666\n")
        return home

    def test_cli_mention_prefix(self):
        home = self._env_home()
        env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": "marek"}
        r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--mention-prefix"], capture_output=True, text=True,
                           env=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "<@444555666> ")

    def test_cli_autopilot_done_dry_run(self):
        home = self._env_home()
        env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": "zbynek"}
        r = subprocess.run(
            [sys.executable, str(self.AIRULESET), "notify", "--autopilot-done",
             "--dry-run", "--repo", "o/cam", "--pr", "88", "--review", "ok",
             "--done", "2", "--remaining", "4", "--tickets-json",
             json.dumps([{"n": 41, "title": "T", "goal": "G", "achieved": "A"}])],
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<@111222333> ", r.stdout)
        self.assertIn("🎯 **Cieľ:** G", r.stdout)
        self.assertIn("✅ **Dosiahnuté:** A", r.stdout)
        self.assertIn("hotové 2 · ostáva 4", r.stdout)

    # --- the shared send path @mentions via the single source of truth -------
    def test_send_hook_prepends_mention_via_cli(self):
        # the @mention now lives in the single shared sender (notify-discord-send.sh),
        # which BOTH the immediate ❓ (Stop) and the idle ✅ (Notification) hooks call
        src = self.SEND_HOOK.read_text()
        self.assertIn("notify --mention-prefix", src)
        # it prepends the resolved mention onto the content it sends
        self.assertIn("MENTION", src)
        # both hooks delegate to it (no duplicated curl)
        self.assertIn("notify-discord-send.sh", self.IDLE_HOOK.read_text())
        pending = (airuleset.REPO_DIR / "hooks" / "notify-discord-pending.sh").read_text()
        self.assertIn("notify-discord-send.sh", pending)


class _FakeTmux:
    """Stand-in for the watchdog's `run` (tmux exec). Answers list-panes /
    capture-pane from canned data and records every send-keys argv."""

    def __init__(self, panes="", captures=None, modes=None, owners=None):
        self.panes = panes
        self.captures = captures or {}
        self.modes = modes or {}          # pane_id -> "1" (in copy-mode) / "0"
        self.owners = owners or {}        # pane_id -> tmux session/group (e.g. marek-12)
        self.sent = []

    def __call__(self, argv, timeout=8):
        if argv[:2] == ["tmux", "list-panes"]:
            return self.panes
        if argv[:2] == ["tmux", "display-message"]:
            pid = argv[argv.index("-t") + 1]
            fmt = argv[-1]
            if fmt == "#{pane_in_mode}":
                return self.modes.get(pid, "0")
            if fmt in ("#{session_group}", "#S"):
                return self.owners.get(pid, "")
            return ""
        if argv[:2] == ["tmux", "capture-pane"]:
            pid = argv[argv.index("-t") + 1]
            return self.captures.get(pid, "")
        if argv[:2] == ["tmux", "send-keys"]:
            self.sent.append(argv)
            return ""
        return ""

    def continues_sent(self):
        # how many `send-keys -l continue` (the literal text, not the Enter) fired
        return sum(1 for a in self.sent if "-l" in a and "continue" in a)


class TestApiWatchdog(TestCase):
    """api-watchdog: detect a Claude Code session stalled on an API error and
    auto-resume it (tmux `continue`), pinging on stall + give-up. Pure logic +
    state machine are unit-tested with no tmux and no network."""

    def setUp(self):
        import watchdog
        self.w = watchdog
        self.tmp = tempfile.mkdtemp()
        self.projects = Path(self.tmp) / "projects"
        self.projects.mkdir()
        self.state = str(Path(self.tmp) / "state.json")
        self.pings = []

    def _send(self, body, owner=None, dedup_key=None, dry_run=False):
        self.pings.append((body, dedup_key, owner))
        return "sent"

    _ERR = {"type": "assistant", "isApiErrorMessage": True,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "API Error: 529 Overloaded"}]}}
    _OK = {"type": "assistant", "isApiErrorMessage": False,
           "message": {"role": "assistant", "content": [{"type": "text", "text": "Hotovo."}]}}
    _SENT = {"type": "assistant",
             "message": {"role": "assistant", "content": [{"type": "text",
                         "text": "No response requested."}]}}

    def _transcript(self, cwd, entries, age_s, now):
        d = self.projects / self.w.encode_project_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / "sess-abc.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        os.utime(p, (now - age_s, now - age_s))
        return p

    # --- pure helpers --------------------------------------------------------
    def test_project_label_expands_generic_checkout_dir(self):
        self.assertEqual(self.w.project_label("/home/newlevel/devel/bakerion-ai/repo"),
                         "bakerion-ai/repo")
        self.assertEqual(self.w.project_label("/home/newlevel/devel/montalu/monday-orders"),
                         "monday-orders")
        self.assertEqual(self.w.project_label("/home/newlevel/devel/restreamer"), "restreamer")

    def test_encode_project_dir_slashes_dots_underscores(self):
        self.assertEqual(
            self.w.encode_project_dir("/home/newlevel/devel/website-newlevel.media"),
            "-home-newlevel-devel-website-newlevel-media")
        # Claude Code also maps '_' -> '-' (real dir on disk)
        self.assertEqual(
            self.w.encode_project_dir("/home/newlevel/devel/tomas_pardubsky/cold_mailing"),
            "-home-newlevel-devel-tomas-pardubsky-cold-mailing")

    def test_transcript_last_error_detects_flagged(self):
        p = self._transcript("/x/p", [{"type": "user", "message": {}}, self._ERR], 600, 1_000_000)
        self.assertIn("529 Overloaded", self.w.transcript_last_error(p))

    def test_transcript_last_error_normal_is_empty(self):
        p = self._transcript("/x/p", [self._ERR, self._OK], 600, 1_000_000)
        self.assertEqual(self.w.transcript_last_error(p), "")

    def test_transcript_last_error_skips_sentinel(self):
        # CC appends a synthetic "No response requested." after the error → still detected
        p = self._transcript("/x/p", [self._ERR, self._SENT], 600, 1_000_000)
        self.assertIn("529", self.w.transcript_last_error(p))

    def test_list_claude_panes_dedups_and_filters(self):
        fake = _FakeTmux(panes="%5\tclaude\t/devel/a\n%5\tclaude\t/devel/a\n"
                               "%6\tbash\t/devel/b\n%7\tclaude\t/devel/c\n")
        self.assertEqual(self.w.list_claude_panes(fake), [("%5", "/devel/a"), ("%7", "/devel/c")])

    # --- decide state machine ------------------------------------------------
    def test_decide_full_lifecycle(self):
        st, now = {}, 1_000_000
        a, e = self.w.decide(st, "k", "h", now, interval=300, max_nudges=3)
        self.assertEqual(a, "nudge"); self.assertEqual(len(e["nudges"]), 1); st["k"] = e
        # too soon → wait
        a, e = self.w.decide(st, "k", "h", now + 100, interval=300, max_nudges=3)
        self.assertEqual(a, "wait"); st["k"] = e
        # after interval → nudge #2
        a, e = self.w.decide(st, "k", "h", now + 300, interval=300, max_nudges=3)
        self.assertEqual(a, "nudge"); self.assertEqual(len(e["nudges"]), 2); st["k"] = e
        # #3
        a, e = self.w.decide(st, "k", "h", now + 600, interval=300, max_nudges=3)
        self.assertEqual(a, "nudge"); self.assertEqual(len(e["nudges"]), 3); st["k"] = e
        # max reached → escalate once
        a, e = self.w.decide(st, "k", "h", now + 900, interval=300, max_nudges=3)
        self.assertEqual(a, "escalate"); self.assertTrue(e["escalated"]); st["k"] = e
        # then noop
        a, e = self.w.decide(st, "k", "h", now + 1200, interval=300, max_nudges=3)
        self.assertEqual(a, "noop")

    def test_decide_new_error_hash_resets(self):
        st = {"k": {"hash": "old", "first_seen": 1, "nudges": [1, 2, 3], "escalated": True}}
        a, e = self.w.decide(st, "k", "NEWHASH", 1_000_000, interval=300, max_nudges=3)
        self.assertEqual(a, "nudge")
        self.assertEqual(e["hash"], "NEWHASH")
        self.assertFalse(e["escalated"])

    # --- run_once integration (fake tmux + fake send) ------------------------
    def test_run_once_nudges_and_notifies_on_stall(self):
        now = 1_000_000
        cwd = "/devel/projx"
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 600, now)  # 10 min stale
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               grace=300, interval=300, max_nudges=3)
        self.assertEqual(fake.continues_sent(), 1, "should send exactly one `continue`")
        self.assertEqual(len(self.pings), 1, "should ping once on the first nudge")
        self.assertIn("projx", self.pings[0][0])         # project name in the alert
        self.assertTrue(any("nudge#1" in l for l in logs))

    def test_run_once_ignores_fresh_transcript(self):
        now = 1_000_000
        cwd = "/devel/fresh"
        self._transcript(cwd, [self._ERR], 30, now)      # only 30s stale → not idle
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self.w.run_once(now=now, run=fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state, grace=300)
        self.assertEqual(fake.continues_sent(), 0)
        self.assertEqual(self.pings, [])

    def test_run_once_ignores_non_error_idle(self):
        now = 1_000_000
        cwd = "/devel/idlechat"
        self._transcript(cwd, [self._OK], 600, now)      # stale but last msg is normal
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self.w.run_once(now=now, run=fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state, grace=300)
        self.assertEqual(fake.continues_sent(), 0)
        self.assertEqual(self.pings, [])

    def test_run_once_ignores_pane_text_only_no_flag(self):
        # REGRESSION (the live incident, 2026-06-20): a session merely DISPLAYING
        # api-error text — quoting "API Error: 529" in a meta-conversation — but
        # whose transcript last assistant msg is NOT isApiErrorMessage must NOT be
        # nudged. Pane content is now irrelevant; ONLY Claude Code's flag triggers.
        now = 1_000_000
        cwd = "/devel/meta"
        self._transcript(cwd, [self._OK], 600, now)      # last msg normal, 10 min stale
        cap = "API Error: 529 Overloaded. This is a server-side issue\n> quoting 529"
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", captures={"%5": cap})
        self.w.run_once(now=now, run=fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state, grace=300)
        self.assertEqual(fake.continues_sent(), 0, "must NOT nudge on pane-text alone")
        self.assertEqual(self.pings, [])

    def test_run_once_escalates_then_stops(self):
        now = 1_000_000
        cwd = "/devel/stuck"
        self._transcript(cwd, [self._ERR], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, interval=300, max_nudges=3)
        self.w.run_once(now=now, **kw)            # nudge #1 + ping
        self.w.run_once(now=now + 300, **kw)      # #2
        self.w.run_once(now=now + 600, **kw)      # #3
        self.w.run_once(now=now + 900, **kw)      # escalate
        self.w.run_once(now=now + 1200, **kw)     # noop
        self.assertEqual(fake.continues_sent(), 3, "exactly 3 continue nudges then stop")
        # 2 pings: first-nudge stall alert + the give-up alert
        self.assertEqual(len(self.pings), 2)
        self.assertIn("pretrváva", self.pings[1][0])

    def test_run_once_drops_recovered_session(self):
        now = 1_000_000
        cwd = "/devel/recov"
        self._transcript(cwd, [self._ERR], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, interval=300, max_nudges=3)
        self.w.run_once(now=now, **kw)
        self.assertTrue(self.w.load_state(self.state), "stalled session recorded")
        # session recovered: transcript now fresh + last msg normal
        self._transcript(cwd, [self._ERR, self._OK], 5, now + 60)
        self.w.run_once(now=now + 60, **kw)
        self.assertEqual(self.w.load_state(self.state), {}, "recovered key dropped")

    def test_run_once_dry_run_sends_nothing(self):
        now = 1_000_000
        cwd = "/devel/dry"
        self._transcript(cwd, [self._ERR], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self.w.run_once(now=now, dry_run=True, run=fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state,
                        grace=300, interval=300, max_nudges=3)
        self.assertEqual(fake.continues_sent(), 0, "dry-run must not send `continue`")

    # --- hardening fixes from the adversarial review -------------------------
    def test_is_usage_cap_classifier(self):
        self.assertTrue(self.w.is_usage_cap("Claude usage limit reached; resets at 5pm"))
        self.assertTrue(self.w.is_usage_cap("You have reached your quota"))
        # transient errors a retry CAN clear must NOT be classified as a usage cap
        self.assertFalse(self.w.is_usage_cap("API Error: 529 Overloaded"))
        self.assertFalse(self.w.is_usage_cap("API Error: rate limited"))

    def test_run_once_skips_ambiguous_cwd(self):
        # two `claude` panes in the SAME cwd → one transcript, can't tell which pane
        # stalled → SKIP (never poke the possibly-healthy pane)
        now = 1_000_000
        cwd = "/devel/shared"
        self._transcript(cwd, [self._ERR], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n%6\tclaude\t" + cwd + "\n")
        logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state, grace=300)
        self.assertEqual(fake.continues_sent(), 0, "ambiguous cwd must NOT be nudged")
        self.assertEqual(self.pings, [])
        self.assertTrue(any("ambiguous" in l for l in logs))

    def test_run_once_skips_pane_in_copy_mode(self):
        # user is scrolling (pane_in_mode=1) → keys would corrupt their selection
        now = 1_000_000
        cwd = "/devel/scrolling"
        self._transcript(cwd, [self._ERR], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", modes={"%5": "1"})
        self.w.run_once(now=now, run=fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state, grace=300)
        self.assertEqual(fake.continues_sent(), 0, "copy-mode pane must NOT be nudged")
        self.assertEqual(self.w.load_state(self.state), {}, "no retry burned while in-mode")

    def test_run_once_usage_cap_pings_no_continue(self):
        now = 1_000_000
        cwd = "/devel/capped"
        cap = {"type": "assistant", "isApiErrorMessage": True,
               "message": {"role": "assistant", "content": [{"type": "text",
                           "text": "Claude usage limit reached — resets at 18:00"}]}}
        self._transcript(cwd, [cap], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, interval=300, max_nudges=3)
        self.w.run_once(now=now, **kw)
        self.assertEqual(fake.continues_sent(), 0, "usage cap must NOT get `continue`")
        self.assertEqual(len(self.pings), 1, "usage cap pings once")
        # and it does NOT keep retrying / never false-'gives up'
        self.w.run_once(now=now + 300, **kw)
        self.w.run_once(now=now + 600, **kw)
        self.assertEqual(fake.continues_sent(), 0)
        self.assertEqual(len(self.pings), 1, "no further pings after the one usage-cap ping")

    def test_ping_dedup_key_includes_first_seen(self):
        # a recovered session that re-stalls later must produce a DISTINCT dedup key
        # (notify's own dedup TTL is 14 days — without first_seen the re-stall ping
        # would be silently swallowed)
        now = 1_000_000
        cwd = "/devel/restall"
        self._transcript(cwd, [self._ERR], 600, now)
        kw = dict(run=_FakeTmux(panes="%5\tclaude\t" + cwd + "\n"), send_fn=self._send,
                  projects_dir=self.projects, state_path=self.state,
                  grace=300, interval=300, max_nudges=3)
        self.w.run_once(now=now, **kw)
        key1 = self.pings[-1][1]
        self.assertIn(str(now), key1, "first_seen must be in the dedup key")
        # recover, then re-stall with the SAME error text much later
        self._transcript(cwd, [self._ERR, self._OK], 5, now + 100)
        self.w.run_once(now=now + 100, **kw)            # drops the recovered key
        self._transcript(cwd, [self._ERR], 600, now + 100000)
        self.w.run_once(now=now + 100000, **kw)
        key2 = self.pings[-1][1]
        self.assertNotEqual(key1, key2, "re-stall must produce a distinct dedup key")

    # --- waiting-on-user (AskUserQuestion) PING-ONLY detector ----------------
    _WAIT_CAP = ("│ ❯ 1. Direction gates\n│ 2. Time + pairing\n"
                 "│ Enter to select · Tab/Arrow keys to navigate · Esc to cancel")

    def test_pane_waiting_on_user_matches_prompt_footer(self):
        self.assertTrue(self.w.pane_waiting_on_user(self._WAIT_CAP))
        self.assertTrue(self.w.pane_waiting_on_user("Do you want to proceed? ❯ 1. Yes"))
        self.assertFalse(self.w.pane_waiting_on_user("● Running tests...\n  42 passed"))
        self.assertFalse(self.w.pane_waiting_on_user(""))

    def test_run_once_pings_waiting_session_never_acts(self):
        now = 1_000_000
        cwd = "/devel/asking"
        self._transcript(cwd, [self._OK], 200, now)   # not flagged; 200s stale
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", captures={"%5": self._WAIT_CAP})
        logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               grace=300, wait_grace=120)
        self.assertEqual(fake.continues_sent(), 0, "waiting must NEVER inject keys")
        self.assertEqual(len(self.pings), 1, "waiting pings once")
        self.assertIn("asking", self.pings[0][0])
        self.assertTrue(self.pings[0][1].startswith("waiting:"))
        self.assertTrue(any("waiting" in l for l in logs))

    def test_run_once_waiting_pings_once_not_every_poll(self):
        now = 1_000_000
        cwd = "/devel/asking2"
        self._transcript(cwd, [self._OK], 200, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", captures={"%5": self._WAIT_CAP})
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, wait_grace=120)
        self.w.run_once(now=now, **kw)
        self.w.run_once(now=now + 60, **kw)
        self.w.run_once(now=now + 120, **kw)
        self.assertEqual(len(self.pings), 1, "one ping per waiting episode, not per poll")

    def test_run_once_waiting_too_fresh_no_ping(self):
        now = 1_000_000
        cwd = "/devel/fresh-ask"
        self._transcript(cwd, [self._OK], 30, now)    # 30s < wait_grace
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", captures={"%5": self._WAIT_CAP})
        self.w.run_once(now=now, run=fake, send_fn=self._send, projects_dir=self.projects,
                        state_path=self.state, grace=300, wait_grace=120)
        self.assertEqual(self.pings, [])

    def test_run_once_waiting_key_dropped_when_answered(self):
        now = 1_000_000
        cwd = "/devel/answered"
        self._transcript(cwd, [self._OK], 200, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", captures={"%5": self._WAIT_CAP})
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, wait_grace=120)
        self.w.run_once(now=now, **kw)
        self.assertIn("wait:sess-abc", self.w.load_state(self.state))
        # answered: prompt footer gone, session moved on
        fake.captures["%5"] = "● Committed abc1234\n  done"
        self.w.run_once(now=now + 60, **kw)
        self.assertNotIn("wait:sess-abc", self.w.load_state(self.state))

    def test_run_once_ping_mentions_pane_owner(self):
        # the ping must @mention the OWNER of the waiting pane (resolved from that
        # pane's tmux session group) — the watchdog runs headless with no tmux of
        # its own, so it can't use the current-context owner
        now = 1_000_000
        cwd = "/devel/ownertest"
        self._transcript(cwd, [self._OK], 200, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         captures={"%5": self._WAIT_CAP}, owners={"%5": "marek-12"})
        self.w.run_once(now=now, run=fake, send_fn=self._send, projects_dir=self.projects,
                        state_path=self.state, grace=300, wait_grace=120)
        self.assertEqual(self.pings[0][2], "marek", "ping must carry the pane's owner")

    # --- wiring --------------------------------------------------------------
    def test_watchdog_subcommand_registered(self):
        self.assertIn("watchdog", airuleset.SUBCOMMANDS)

    def test_validate_watchdog_clean(self):
        self.assertEqual(airuleset._validate_watchdog(), [])

    def test_service_template_runs_watchdog_once(self):
        svc = (airuleset.REPO_DIR / "settings" / "api-watchdog.service.template").read_text()
        self.assertIn("watchdog --once", svc)
        self.assertIn("{{REPO_DIR}}", svc)
