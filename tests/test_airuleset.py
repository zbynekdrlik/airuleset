"""Tests for airuleset CLI."""

import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import TestCase, TestResult
from unittest import mock as m

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Add this file's own directory so sibling test-support modules import as
# top-level names (mirrors test_ask_before_assuming_dropped_rows.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset
import cli_remote  # noqa: E402  (#433 L-E seam re-target)
from _hook_state_cleanup import sweep_session_files  # noqa: E402


def _path_without_python3():
    """A PATH directory (symlinks to everything in /usr/bin except python3*)
    for reproducing a hook's embedded-python3-child failing to launch —
    used by the exit-code-discipline tests (a hook malfunction, e.g.
    python3 missing, must never be reported as a real content violation
    with an empty reason)."""
    tmpbin = tempfile.mkdtemp()
    src_dir = "/usr/bin"
    for name in os.listdir(src_dir):
        if name.startswith("python3"):
            continue
        try:
            os.symlink(os.path.join(src_dir, name), os.path.join(tmpbin, name))
        except OSError as e:
            print("symlink skipped for %s: %r" % (name, e))
    return tmpbin


def _path_with_failing_heredoc_python3():
    """Like `_path_without_python3`, but installs a FAKE `python3` shim
    instead of removing it entirely: `python3 -c ...` invocations (the
    early JSON-payload-parsing step every hook does first) still delegate
    to the REAL python3 and succeed, while a `python3 - ... <<HEREDOC`
    invocation (script fed via stdin, no `-c` — the content-scanning
    child every hook runs SECOND) exits 42 without running anything. Some
    hooks (e.g. block-test-skips.sh) parse the tool payload with the FIRST
    shape before ever reaching the "is this even a push?" gate — killing
    python3 entirely makes that early step fall back in a way that never
    reaches the code path this test targets. This shim isolates failure to
    exactly the second, content-scanning invocation."""
    tmpbin = tempfile.mkdtemp()
    src_dir = "/usr/bin"
    for name in os.listdir(src_dir):
        if name.startswith("python3"):
            continue
        try:
            os.symlink(os.path.join(src_dir, name), os.path.join(tmpbin, name))
        except OSError as e:
            print("symlink skipped for %s: %r" % (name, e))
    wrapper = os.path.join(tmpbin, "python3")
    with open(wrapper, "w") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            "for a in \"$@\"; do\n"
            "    if [ \"$a\" = \"-c\" ]; then exec /usr/bin/python3 \"$@\"; fi\n"
            "done\n"
            "exit 42\n"
        )
    os.chmod(wrapper, 0o755)
    return tmpbin


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

    def test_user_questions_slovak_rule_present(self):
        # AskUserQuestion dialogs shown IN Claude must be Slovak + plain human language
        mod = airuleset.REPO_DIR / "modules" / "core" / "user-questions-slovak.md"
        self.assertTrue(mod.exists(), "user-questions-slovak.md missing")
        text = mod.read_text()
        self.assertIn("SLOVAK", text)
        self.assertIn("AskUserQuestion", text)
        # explain each ticket (no bare number/range) + ask in small parts, iterate
        self.assertIn("NEVER a bare number or range", text)
        self.assertIn("one decision at a time", text)
        # wired into the global config so it applies to every project
        prof = airuleset.UNIVERSAL_PROFILE.read_text()
        self.assertIn("modules/core/user-questions-slovak.md", prof)

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
        # #317 (2026-08-08): fleet dispatch (parallel isolation:worktree workers) is
        # now the DEFAULT — the old "serial per repo" (one worker at a time, full
        # stop) invariant this test used to require is exactly what #317 overturns.
        # What must still hold is INTEGRATION staying strictly serial per repo.
        self.assertIn("serial integration per repo", s.lower())
        self.assertIn('isolation: "worktree"', s)

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
        self.assertIn("fired by the WORKER", s)
        # card carries the deployed version, not the PR number
        self.assertIn("--version", s)
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
    CLEAR = airuleset.REPO_DIR / "hooks" / "clear-question-dedup.sh"
    _n = 0

    def _sid(self):
        # (#293) sweep_session_files is shape-agnostic (globs "*<sid>*") --
        # never hand-enumerate marker paths here again, or a FUTURE new
        # marker (like CARDCHK was) silently reopens this same leak.
        TestDiscordNotifyHooks._n += 1
        sid = f"test-dn-{os.getpid()}-{TestDiscordNotifyHooks._n}"
        p = f"/tmp/claude-discord-pending-{sid}"
        self.addCleanup(sweep_session_files, sid)
        return sid, p

    def _user_prompt(self, sid):
        # simulate the UserPromptSubmit hook firing (the user actually typed)
        payload = json.dumps({"session_id": sid, "prompt": "odpoveď"})
        return subprocess.run(["bash", str(self.CLEAR)], input=payload, text=True,
                              capture_output=True)

    def _stop(self, sid, msg, cwd="", owner="", home=None):
        # Hermetic: DRYRUN + ND_DRYRUN_FILE → the ❓ immediate-send composes to a
        # file (never a real Discord POST, never stdout). _sent() reads that file.
        sf = f"/tmp/claude-dn-send-{sid}"
        self.addCleanup(lambda: os.path.exists(sf) and os.remove(sf))
        if os.path.exists(sf):
            os.remove(sf)
        self._send_file = sf
        # TMUX_PANE="" keeps goal_armed() deterministic here (never shells out
        # to the REAL tmux pane this test suite happens to run inside) — see
        # TestGoalArmedSuppressesIdlePing for the dedicated ND_FAKE_PANE_CAPTURE
        # coverage of that check.
        env = {**os.environ, "DISCORD_NOTIFY_DRYRUN": "1", "ND_DRYRUN_FILE": sf,
               "AIRULESET_NOTIFY_OWNER": owner, "ND_BLOCK_SETTLE": "0",
               "TMUX_PANE": ""}
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

    def test_identical_question_repeat_is_deduped(self):
        # A /goal-loop re-poke of a session STILL blocked on the SAME unanswered
        # question re-emits the identical ❓ line every re-poked turn — each one
        # re-pinged the phone (the 9× "rovnaká otázka ako predtým" restreamer spam,
        # 2026-07-04). The FIRST ask pings; the identical repeat must NOT.
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        q = "❓ NEEDS YOU: #280 (záloha, odporúčam) alebo najprv 0.28.0?"
        self._stop(sid, q, cwd=cwd)
        self.assertIn("#280", self._sent(), "the FIRST ask must always ping")
        self._stop(sid, "Stojím len na tvojom rozhodnutí.\n\n" + q, cwd=cwd)
        self.assertEqual(self._sent(), "",
                         "identical repeated question must be deduped, not re-pinged")

    def test_different_question_always_pings(self):
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        self._stop(sid, "❓ NEEDS YOU: #280 alebo 0.28.0?", cwd=cwd)
        self.assertIn("#280", self._sent())
        self._stop(sid, "❓ NEEDS YOU: mám zmazať starú zálohu?", cwd=cwd)
        self.assertIn("zmazať starú zálohu", self._sent(),
                      "a DIFFERENT question must always ping — dedup is per-content")

    def test_user_prompt_records_presence_marker(self):
        # the UserPromptSubmit hook stamps /tmp/claude-user-active-<sid> — the
        # question-quality gate reads it to skip phone-shape enforcement while
        # the user is PRESENT at the terminal (camera-box "Hruza", 2026-07-05)
        sid, _ = self._sid()
        f = f"/tmp/claude-user-active-{sid}"
        self.addCleanup(lambda: os.path.exists(f) and os.remove(f))
        r = self._user_prompt(sid)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(os.path.exists(f), "presence marker must be stamped")

    def test_user_prompt_clears_question_dedup(self):
        # After the user actually TYPES, the conversation moved on — a fresh ask
        # must ping again even if its text is byte-identical to the old one.
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        q = "❓ NEEDS YOU: #280 alebo 0.28.0?"
        self._stop(sid, q, cwd=cwd)
        self.assertIn("#280", self._sent())
        r = self._user_prompt(sid)                 # UserPromptSubmit clears LASTQ
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "", "clear hook must be silent")
        self._stop(sid, q, cwd=cwd)
        self.assertIn("#280", self._sent(),
                      "after a real user prompt the same text is a FRESH ask → ping")

    def test_failed_delivery_is_not_recorded_as_pinged(self):
        # Review finding (2026-07-04): LASTQ was written BEFORE the fire-and-forget
        # send — a transient Discord failure on the FIRST ask would then suppress
        # every identical re-emit forever (question never reached the phone, and
        # watchdog job-2 has no backstop for a text-marker ❓). The ❓ path now
        # confirms delivery (ND_CONFIRM): a failed send leaves LASTQ unwritten so
        # the next identical re-emit RETRIES.
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        home = tempfile.mkdtemp()      # no ~/.claude/channels/discord/.env → no token
        q = "❓ NEEDS YOU: #280 alebo 0.28.0?"
        payload = json.dumps({"session_id": sid, "last_assistant_message": q,
                              "cwd": cwd})
        subprocess.run(["bash", str(self.PENDING)], input=payload, text=True,
                       capture_output=True,
                       env={**os.environ, "HOME": home,
                            "DISCORD_NOTIFY_DRYRUN": "0",
                            "AIRULESET_NOTIFY_OWNER": "", "ND_BLOCK_SETTLE": "0"})
        self.assertFalse(os.path.exists(f"/tmp/claude-discord-lastq-{sid}"),
                         "a FAILED delivery must NOT be recorded as pinged")
        # the identical re-emit retries — and once delivery works, it pings
        self._stop(sid, q, cwd=cwd)
        self.assertIn("#280", self._sent(),
                      "the retry of a never-delivered question must ping")

    def test_done_line_with_midline_marker_chars_is_done_not_question(self):
        # LIVE incident (2026-07-04): a final line "✅ DONE: odpoveď na Discord ❓
        # ping sa…" was mis-classified as a QUESTION (loose `grep -q "❓"` matched
        # the mid-sentence ❓ character) and pinged "otázka" with garbled content.
        # Marker detection must anchor to the LINE START — mid-line ❓/⏳ is prose.
        sid, p = self._sid()
        cwd = tempfile.mkdtemp()
        self._stop(sid, "✅ DONE: odpoveď na Discord ❓ ping (predtým ⏳) sa "
                        "doručí do správnej session", cwd=cwd)
        self.assertEqual(self._sent(), "", "mid-line ❓ is prose — must NOT ❓-ping")
        self.assertTrue(os.path.exists(p), "must record the pending ✅ instead")
        with open(p) as fh:
            self.assertIn("✅", fh.read())

    def test_asked_line_identical_repeat_is_deduped(self):
        # Same dedup on the ask-and-continue form (❓ ASKED + ⏳ WORKING).
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        msg = ("❓ ASKED: reset EQ na 0 dB alebo posledný preset?\n\n"
               "⏳ WORKING: pokračujem na #12")
        self._stop(sid, msg, cwd=cwd)
        self.assertIn("reset EQ", self._sent(), "the first ❓ ASKED must ping")
        self._stop(sid, msg, cwd=cwd)
        self.assertEqual(self._sent(), "",
                         "identical re-raised ❓ ASKED must be deduped")

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

    def test_question_label_stripped_bold_preserved(self):
        # the marker label (**Question:** / NEEDS YOU / ASKED) is stripped, but
        # markdown BOLD in the question itself is PRESERVED — stripping every
        # ** rendered the phone question as a flat unformatted wall (the
        # camera-box "ziadne zvyraznenia fontu" complaint, 2026-07-05)
        sid, p = self._sid()
        self._stop(sid, "## ✅ Work Complete\n\n❓ **Question:** schváliš merge PR #5?")
        sent = self._sent()
        self.assertIn("❓", sent)
        self.assertIn("schváliš merge PR #5?", sent)
        self.assertNotIn("Question:", sent)      # label stripped
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

    def test_question_always_pings_even_mid_loop(self):
        # A genuine question ALWAYS reaches the phone — there is NO suppression.
        # The old behavior (❓ + "continuing" language → swallow the ping) was the
        # exact bug the user reported: a mid-loop question that never pinged, then a
        # reproach hours later. Removed. Continuing is fine; the ping is not optional.
        for cont in ["Remaining backlog (14). I can keep grinding these.",
                     "PP OAuth is out to your phone; continuing now with #426.",
                     "I'll surface the blocked trio later; moving on to the next ticket."]:
            sid, _ = self._sid()
            self._stop(sid, cont + "\n\n❓ NEEDS YOU: čo s 3 blokovanými ticketmi?")
            self.assertIn("❓", self._sent(), "❓ must ping even mid-loop: %r" % cont)
        # a genuine ❓ (no continuing language) DOES ping too
        sid2, _ = self._sid()
        self._stop(sid2, "❓ NEEDS YOU: schváliš merge PR #5?")
        self.assertIn("❓", self._sent())

    def test_long_question_survives_untruncated(self):
        # LIVE incident (2026-07-04, codex-bridge): a full self-contained Slovak
        # question (briefing + the actual ask, ~1100 chars) reached the phone CUT
        # at 250 chars mid-word ("…sklad zač") — the intro arrived, the QUESTION
        # never did. Discord allows ~2000 chars/message; the delivery must carry
        # the whole question.
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        q = ("❓ NEEDS YOU: V projekte codex-bridge (prenos dát z Codexu do Odoo) "
             + "— cca 47 000 objednávok z rokov 2024/2025 bolo reálne doručených, "
               "ale Codex k nim nemá dodací list, takže v Odoo ukazujú "
               "„doručené = 0“. " * 8
             + "Mám ich v Odoo označiť ako doručené jednorazovým skriptom?")
        self._stop(sid, q, cwd=cwd)
        self.assertIn("označiť ako doručené jednorazovým skriptom?", self._sent(),
                      "the actual question at the END must reach the phone")

    def test_question_block_above_marker_is_delivered(self):
        # The rules mandate a SELF-CONTAINED question (briefing + options) written
        # as the contiguous block ending with the ❓ marker line. The phone ping
        # must carry that WHOLE block — not just the bare marker line (the user's
        # complaint: "naraz príde otázka, nemá úvod, nemá súvislosti").
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        msg = ("dlhá pracovná analýza, ktorá na telefón nepatrí\n\n"
               "**Otázka — projekt codex-bridge (prenos dát z Codexu do Odoo):** "
               "47 000 starých objednávok je reálne doručených, ale v Odoo "
               "ukazujú „doručené = 0“.\n"
               "• Označiť ich skriptom ako doručené (odporúčam) — rýchle\n"
               "• Nechať tak — historické čísla ostanú nulové\n"
               "❓ NEEDS YOU: označiť skriptom, alebo nechať tak?")
        self._stop(sid, msg, cwd=cwd)
        sent = self._sent()
        self.assertIn("prenos dát z Codexu do Odoo", sent)   # briefing arrived
        self.assertIn("Označiť ich skriptom", sent)          # options arrived
        self.assertIn("nechať tak?", sent)                   # the decision line
        self.assertNotIn("dlhá pracovná analýza", sent,
                         "prose above the question block must NOT be pulled in")

    def test_short_marker_pulls_previous_paragraph_as_context(self):
        # A short bare marker after a blank line still needs its context — the
        # paragraph directly above IS the explanation, deliver it too.
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        msg = ("Nasadenie čaká len na tvoje rozhodnutie o zálohe pred migráciou "
               "databázy (trvá ~10 minút navyše).\n\n"
               "❓ NEEDS YOU: spraviť zálohu pred migráciou?")
        self._stop(sid, msg, cwd=cwd)
        sent = self._sent()
        self.assertIn("rozhodnutie o zálohe pred migráciou", sent)
        self.assertIn("spraviť zálohu pred migráciou?", sent)

    def test_question_block_rendered_as_discord_list_with_spacing(self):
        # `•` options become real Discord list items (`- `), blank lines
        # separate briefing / options / decision, the briefing line and the
        # final decision are BOLD — the phone must see structure, never a
        # text wall ("ziadne odrazky, ziadne zvyraznenia", 2026-07-05)
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        msg = ("Otázka — projekt demo (ukážka): kontext v prvej vete, aby bol "
               "blok dosť dlhý na doručenie bez naťahovania odstavca vyššie.\n"
               "• Možnosť A (odporúčam) — rýchle\n"
               "• Možnosť B — pomalšie\n"
               "❓ NEEDS YOU: A alebo B?")
        self._stop(sid, msg, cwd=cwd)
        sent = self._sent()
        self.assertIn("**Otázka — projekt demo (ukážka):**", sent)  # auto-bold header
        # options NUMBERED — the user answers with just "1"/"2"; a reply "áno"
        # was ambiguous between the offers (user, 2026-07-05)
        self.assertIn("\n1. Možnosť A (odporúčam) — rýchle", sent)
        self.assertIn("\n2. Možnosť B — pomalšie", sent)
        self.assertNotIn("•", sent)
        self.assertNotIn("\n- ", sent)
        self.assertIn("\n\n1. Možnosť A", sent)      # blank line before options
        self.assertIn("\n\n❓ **A alebo B?**", sent)  # blank line + bold decision
        self.assertIn("číslo možnosti (1/2)", sent)   # reply hint below decision

    def test_bare_question_without_options_gets_no_reply_hint(self):
        # a plain one-line question (present-user dialog) must not grow a
        # "číslo možnosti" hint — there are no numbered options to point at
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        self._stop(sid, "❓ NEEDS YOU: schváliš merge PR #5?", cwd=cwd)
        self.assertNotIn("číslo možnosti", self._sent())

    def test_already_numbered_options_kept_and_hinted(self):
        # a session that numbered its options itself keeps its numbering
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        msg = ("**Otázka — projekt demo (ukážka):** krátky kontext k rozhodnutiu "
               "o možnostiach, nech je blok samostatný.\n"
               "1. Prvá možnosť (odporúčam)\n"
               "2. Druhá možnosť\n"
               "❓ NEEDS YOU: prvá alebo druhá?")
        self._stop(sid, msg, cwd=cwd)
        sent = self._sent()
        self.assertIn("\n1. Prvá možnosť (odporúčam)", sent)
        self.assertIn("\n2. Druhá možnosť", sent)
        self.assertIn("číslo možnosti (1/2)", sent)

    def test_oversize_question_keeps_decision_line(self):
        # >1800 chars: truncation must never cut the final DECISION away (the
        # live failure was exactly an intro whose question got chopped off).
        # Head is kept, the tail of the marker line is re-appended, and the
        # whole device line stays under Discord's 2000-char message cap.
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        q = ("❓ NEEDS YOU: " + "veľmi dlhý kontext o projekte a migrácii. " * 80
             + "Rozhodnutie: migrovať hneď?")
        self._stop(sid, q, cwd=cwd)
        sent = self._sent()
        self.assertIn("migrovať hneď?", sent,
                      "the decision at the END must survive truncation")
        self.assertLessEqual(len(sent.strip()), 2000,
                             "device line must fit Discord's message cap")

    def test_dedup_keys_on_marker_line_not_surrounding_context(self):
        # The dedup (one ping per DISTINCT question) keys on the ❓ marker LINE —
        # a /goal re-poke repeats the marker verbatim but the surrounding turn
        # text differs; that must still dedup, not re-ping.
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        q = "❓ NEEDS YOU: spraviť zálohu pred migráciou?"
        self._stop(sid, "Kontext pokusu č. 1 o vysvetlenie.\n\n" + q, cwd=cwd)
        self.assertIn("zálohu", self._sent(), "the FIRST ask must ping")
        self._stop(sid, "Úplne iný sprievodný text po re-poke.\n\n" + q, cwd=cwd)
        self.assertEqual(self._sent(), "",
                         "identical marker line must dedup despite changed prose")

    def test_manyline_question_near_cap_keeps_decision_line(self):
        # Review finding (2026-07-04, MEDIUM): the send-path 2000-char cap was
        # a blind HEAD slice applied AFTER per-line '> ' quoting — a many-line
        # question under the payload budget still inflated past the cap and
        # lost its FINAL decision line (the exact failure class this fixes).
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        lines = ["• T%04d ab" % i for i in range(152)]
        msg = "\n".join(["**Otázka — projekt demo:** dlhý zoznam ticketov."]
                        + lines + ["❓ NEEDS YOU: migrovať všetko naraz?"])
        self._stop(sid, msg, cwd=cwd)
        sent = self._sent()
        self.assertIn("migrovať všetko naraz?", sent,
                      "the decision line must survive the send-path cap")
        self.assertLessEqual(len(sent.strip()), 2000)

    def test_diacritic_heavy_short_marker_still_pulls_context(self):
        # Review finding (2026-07-04, LOW): mawk length() counts BYTES — a
        # short (<200 chars) but diacritic-heavy Slovak marker measured ≥200
        # "long" and silently lost its briefing paragraph. Gate on CHARACTERS.
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        q = "žšťčďňáéíóúý" * 10                    # 120 chars, 240 bytes
        msg = ("Kontext: rozhodnutie o žalúziách v zasadačke, treba tvoj "
               "súhlas.\n\n"
               "❓ NEEDS YOU: " + q)
        self._stop(sid, msg, cwd=cwd)
        self.assertIn("rozhodnutie o žalúziách", self._sent(),
                      "the briefing must ride along (count chars, not bytes)")

    def _touch_block(self, name, sid, age=0):
        f = f"/tmp/airuleset-{name}-block-{sid}"
        with open(f, "w") as fh:
            fh.write("1")
        if age:
            old = time.time() - age
            os.utime(f, (old, old))
        self.addCleanup(lambda: os.path.exists(f) and os.remove(f))
        return f

    def test_rejected_draft_attempt_does_not_ping(self):
        # Stop hooks run in PARALLEL: when a blocking gate rejects the turn
        # (fresh /tmp/airuleset-*-block-<sid>), the pending hook must NOT ping
        # that draft — camera-box got 3 pings in 3 minutes for ONE question
        # because every rejected rewrite attempt pinged too (2026-07-05).
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        blk = self._touch_block("question-quality", sid)
        self._stop(sid, "❓ NEEDS YOU: draft otázky, ktorý gate zamietol?", cwd=cwd)
        self.assertEqual(self._sent(), "", "a rejected draft must not ping")
        self.assertFalse(os.path.exists(f"/tmp/claude-discord-lastq-{sid}"),
                         "suppressed draft must stay retryable")
        # the accepted rewrite (gate passed → removed its block file) pings
        os.remove(blk)
        self._stop(sid, "❓ NEEDS YOU: finálna verzia otázky?", cwd=cwd)
        self.assertIn("finálna verzia", self._sent())

    def test_stale_block_file_does_not_suppress(self):
        # an old leftover retry file (earlier turn, other gate) must not
        # swallow a legitimate question
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        self._touch_block("status-marker", sid, age=120)
        self._stop(sid, "❓ NEEDS YOU: normálna otázka po dlhšom čase?", cwd=cwd)
        self.assertIn("normálna otázka", self._sent())

    def test_reworded_unanswered_question_edits_in_place(self):
        # a REWORD of a still-unanswered question must EDIT the existing
        # Discord message (edits do not push-ping) — never post a new ping
        # (the 3-pings-for-one-question camera-box spam, 2026-07-05)
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        self._stop(sid, "❓ NEEDS YOU: prvá verzia otázky?", cwd=cwd)
        self.assertIn("prvá verzia", self._sent())
        self._stop(sid, "❓ NEEDS YOU: prepísaná verzia tej istej otázky?", cwd=cwd)
        sent = self._sent()
        self.assertIn("[edit]", sent, "a reword must EDIT, not repost")
        self.assertIn("prepísaná verzia", sent)
        self.assertNotIn("— otázka", sent, "no fresh POST header on a reword")

    def test_new_question_after_user_prompt_posts_fresh(self):
        # once the user actually TYPED, the next ask is a genuinely new
        # question → a fresh POST (fresh push ping), not an edit
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        self._stop(sid, "❓ NEEDS YOU: prvá otázka?", cwd=cwd)
        self._user_prompt(sid)
        self._stop(sid, "❓ NEEDS YOU: druhá, úplne iná otázka?", cwd=cwd)
        sent = self._sent()
        self.assertIn("druhá, úplne iná otázka?", sent)
        self.assertNotIn("[edit]", sent)

    def test_asked_line_pulls_its_context_paragraph(self):
        # ask-and-continue: the ❓ ASKED ping carries the explanation paragraph
        # above it, but never the ⏳ continuation below.
        sid, _ = self._sid()
        cwd = tempfile.mkdtemp()
        msg = ("Ticket #58 (kontrola pred štartom) potrebuje tvoje rozhodnutie "
               "o predvolenej hodnote EQ.\n\n"
               "❓ ASKED: reset na 0 dB alebo posledný preset?\n\n"
               "⏳ WORKING: medzitým robím #59 (nezávislé od odpovede)")
        self._stop(sid, msg, cwd=cwd)
        sent = self._sent()
        self.assertIn("rozhodnutie o predvolenej hodnote EQ", sent)
        self.assertIn("reset na 0 dB", sent)
        self.assertNotIn("WORKING", sent)
        self.assertNotIn("#59", sent)

    def test_asked_line_pings_while_turn_continues_working(self):
        # ask-and-continue: the turn raises a per-ticket question (pings + tracked
        # on the ticket) and ENDS ⏳ WORKING because it keeps doing other answer-
        # independent work. The ❓ ASKED body line must fire the ping even though the
        # terminal marker is ⏳ (which alone would clear pending). Precedence: ASKED
        # over the trailing ⏳.
        sid, p = self._sid()
        self._stop(sid, "❓ ASKED: #58 (kontrola pred štartom) — reset na 0 dB "
                        "alebo posledný preset?\n\n"
                        "⏳ WORKING: medzitým robím #59, #60 (nezávislé od odpovede)")
        sent = self._sent()
        self.assertIn("❓", sent)
        self.assertIn("reset na 0 dB", sent)
        self.assertNotIn("ASKED", sent)          # the label is stripped from the ping
        self.assertFalse(os.path.exists(p), "❓ ASKED must not leave a pending")

    def test_asked_line_bold_markdown_form_pings(self):
        # tolerate the bold form "❓ **ASKED:** <q>"
        sid, _ = self._sid()
        self._stop(sid, "❓ **ASKED:** schváliš nový layout?\n\n⏳ WORKING: robím #61")
        self.assertIn("schváliš nový layout?", self._sent())

    def test_immediate_question_is_structured_markdown(self):
        # the ❓ device line must be Discord-markdown structured: bold header,
        # blank separator, UNQUOTED body — a `> ` blockquote renders the whole
        # question as one gray wall ("velky nekonecny text", 2026-07-05)
        sid, p = self._sid()
        cwd = tempfile.mkdtemp()
        self._stop(sid, "❓ NEEDS YOU: reset na 0 dB alebo posledný preset?", cwd=cwd)
        sent = self._sent().strip()
        lines = sent.split("\n")
        self.assertTrue(lines[0].startswith("**❓"), lines[0])  # bold header
        self.assertIn(os.path.basename(cwd), lines[0])          # project in header
        self.assertIn("otázka", lines[0])                        # Slovak status
        self.assertEqual(lines[1], "", "blank separator after the header")
        self.assertIn("posledný preset?", sent)
        self.assertFalse(any(ln.startswith("> ") for ln in lines),
                         "❓ body must NOT be blockquoted (gray-wall rendering)")

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

    def _mirror_home(self):
        # a persona box: owner=david, mirrored to zbynek. Each has its own thread +
        # @mention so both people get the notification in their OWN thread.
        home = tempfile.mkdtemp()
        d = Path(home) / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        (d / ".env").write_text(
            "DISCORD_MENTION_DAVID=90000\nDISCORD_MENTION_ZBYNEK=10000\n"
            "DISCORD_NOTIFICATION_CHANNEL_DAVID=dthread\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=zthread\n"
            "DISCORD_MIRROR_DAVID=zbynek\n")
        return home

    def test_shell_send_mirrors_question_to_parallel_owner(self):
        # The shell send path (used by the ❓/✅ hooks) must fan out to the primary
        # owner AND every DISCORD_MIRROR_<OWNER> — david's ❓ ALSO reaches zbynek's
        # thread with zbynek's @mention. Two blocks in the dry-run file, primary first.
        sid, _p = self._sid()
        self._stop(sid, "❓ NEEDS YOU: reštartovať most?",
                   owner="david", home=self._mirror_home())
        sent = self._sent()
        blocks = [b for b in sent.split("<@") if b.strip()]
        self.assertEqual(len(blocks), 2, f"expected david + zbynek blocks: {sent!r}")
        self.assertIn("<@90000> ", sent)    # david's own @mention
        self.assertIn("<@10000> ", sent)    # zbynek mirror @mention
        self.assertTrue(sent.startswith("<@90000> "), "primary (david) not first")

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


class TestGoalArmedSuppressesIdlePing(TestCase):
    """A per-ticket ✅ DONE must not queue a SECOND idle Discord ping when the
    sanctioned per-ticket run-card ALREADY gave phone visibility for that
    ticket — that second ping is the per-phase noise the user removed
    (milestone-notifications.md, 2026-07-25 revision). An armed `/goal` is
    detected via the SAME `◎ /goal` signal the watchdog's own goal jobs key
    on (`_safe_to_bounce_nudge` / `goal_autoarm`), captured from THIS
    session's own pane — never a second, invented detector. Only the ROUTINE
    ✅ is guarded; a genuine ❓ question always pings.

    #134 CORRECTED THE CONDITION and these tests were rewritten with it. The
    armed goal alone is NOT sufficient: nothing enforced the card it defers
    to, and when workers drifted out of firing it the suppression removed the
    only remaining signal — five days, ~85 merged PRs, zero reports. A ✅ is
    now dropped only when a card was actually DELIVERED for this repo since
    the previous ✅ boundary. The delivery-conditional behaviour is covered in
    full by `tests/test_run_card_enforcement.py`
    (TestSuppressionIsConditionalOnDelivery); what remains here is the arm
    that never changed — no armed goal, always ping — plus the ❓ carve-out."""

    PENDING = airuleset.REPO_DIR / "hooks" / "notify-discord-pending.sh"
    _n = 0

    def _sid(self):
        # (#293) sweep_session_files is shape-agnostic (globs "*<sid>*") --
        # this used to hand-enumerate ONLY the pending marker, so it never
        # cleaned lastq (written by the ❓ test in this class) or cardchk
        # (written by every ✅ turn) -- 430 + 681 real leftover files on
        # this box. Never hand-enumerate marker paths here again.
        TestGoalArmedSuppressesIdlePing._n += 1
        sid = "test-ga-%d-%d" % (os.getpid(), TestGoalArmedSuppressesIdlePing._n)
        p = f"/tmp/claude-discord-pending-{sid}"
        self.addCleanup(sweep_session_files, sid)
        return sid, p

    def _stop(self, sid, msg, pane_capture=None, cwd=""):
        env = {**os.environ, "DISCORD_NOTIFY_DRYRUN": "1",
               "AIRULESET_NOTIFY_OWNER": "", "TMUX_PANE": ""}
        if pane_capture is not None:
            env["ND_FAKE_PANE_CAPTURE"] = pane_capture
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": cwd})
        return subprocess.run(["bash", str(self.PENDING)], input=payload, text=True,
                              capture_output=True, env=env)

    def test_armed_goal_alone_no_longer_suppresses_the_pending_ping(self):
        # Was `test_armed_goal_suppresses_the_pending_ping`, asserting the
        # premise #134 identifies as the design error. With no resolvable
        # repo (cwd="") delivery cannot be PROVEN, and an unprovable
        # suppression is what produced five days of silence — so the ping
        # goes through. The suppress-when-delivered arm lives in
        # test_run_card_enforcement.py against a real repo + real marker.
        sid, p = self._sid()
        self._stop(sid, "## ✅ Work Complete\n\n✅ DONE: #42 zmergnuté -> v1.2.3",
                   pane_capture="◎ /goal   ctx 50K\n")
        self.assertTrue(os.path.exists(p),
                        "an armed goal is not evidence a card was delivered")

    def test_no_armed_goal_still_queues_the_ping(self):
        sid, p = self._sid()
        self._stop(sid, "## ✅ Work Complete\n\n✅ DONE: #42 zmergnuté -> v1.2.3",
                   pane_capture="ctx 50K\n")   # no goal indicator in the pane
        self.assertTrue(os.path.exists(p))
        self.assertIn("zmergnuté", open(p).read())

    def test_missing_pane_capture_defaults_to_not_armed(self):
        # no ND_FAKE_PANE_CAPTURE override and TMUX_PANE="" → real tmux is
        # never invoked, and the absence of any signal must default to
        # "not armed" (the normal single-shot / non-autopilot session case).
        sid, p = self._sid()
        self._stop(sid, "✅ DONE: hotovo")
        self.assertTrue(os.path.exists(p))

    def test_question_still_pings_regardless_of_an_armed_goal(self):
        # the goal-armed guard applies ONLY to the routine ✅ idle ping — a
        # genuine question must always ping, armed goal or not.
        sid, _ = self._sid()
        r = self._stop(sid, "❓ NEEDS YOU: schváliš merge PR #5?",
                       pane_capture="◎ /goal\n")
        self.assertEqual(r.returncode, 0)


class TestNotifyMarkerCleanupCoversEveryShape(TestCase):
    """(#293) TestDiscordNotifyHooks._sid / TestGoalArmedSuppressesIdlePing._sid
    each hand-enumerated the exact /tmp marker shapes notify-discord-pending.sh
    can create instead of using tests/_hook_state_cleanup.py's
    sweep_session_files (built for precisely this class of bug, #202) — when
    #161 added a NEW marker (CARDCHK, the delivery-conditional-suppression
    checkpoint written on EVERY ✅ DONE turn) neither hand-written list was
    updated. Live proof on this box (measured 2026-08-07, spanning several
    days of unittest runs on a shared machine): 2061 leftover
    /tmp/claude-discord-cardchk-test-dn-* files, 681 test-ga-* ones, plus 430
    /tmp/claude-discord-lastq-test-ga-* (TestGoalArmedSuppressesIdlePing never
    cleaned lastq at all — only ❓ turns write it, and its own
    test_question_still_pings_regardless_of_an_armed_goal sends one).

    This drives ONE real turn from each class end-to-end and asserts NO
    /tmp/claude-discord-* marker of ANY shape survives its teardown — the
    sweep-based fix is shape-agnostic by design, so a FUTURE new marker can
    never silently reopen this the same way CARDCHK did."""

    def _run_and_capture_sid(self, cls, method_name):
        captured = {}
        orig_sid = cls._sid

        def spy(self_inner):
            result = orig_sid(self_inner)
            captured["sid"] = result[0] if isinstance(result, tuple) else result
            return result

        with m.patch.object(cls, "_sid", spy):
            case = cls(method_name)
            result = TestResult()
            case.run(result)
        self.assertTrue(result.wasSuccessful(),
                        (result.failures, result.errors))
        self.assertIn("sid", captured, "the test never called _sid() at all")
        return captured["sid"]

    def test_discord_notify_hooks_sid_leaves_no_marker_after_a_done_turn(self):
        sid = self._run_and_capture_sid(
            TestDiscordNotifyHooks, "test_done_multiline_report_records")
        leftover = glob.glob("/tmp/claude-discord-*-" + sid)
        self.assertEqual(leftover, [], leftover)

    def test_goal_armed_sid_leaves_no_marker_after_a_question_turn(self):
        sid = self._run_and_capture_sid(
            TestGoalArmedSuppressesIdlePing,
            "test_question_still_pings_regardless_of_an_armed_goal")
        leftover = glob.glob("/tmp/claude-discord-*-" + sid)
        self.assertEqual(leftover, [], leftover)

    def test_goal_armed_sid_leaves_no_marker_after_a_done_turn(self):
        sid = self._run_and_capture_sid(
            TestGoalArmedSuppressesIdlePing,
            "test_no_armed_goal_still_queues_the_ping")
        leftover = glob.glob("/tmp/claude-discord-*-" + sid)
        self.assertEqual(leftover, [], leftover)


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
        import subprocess
        import shutil
        import stat
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        repo = os.path.join(root, "repo")
        bind = os.path.join(root, "bin")
        os.makedirs(repo)
        os.makedirs(bind)
        def g(*a):
            return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)
        g("init", "-q", "-b", "dev")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        open(os.path.join(repo, "f"), "w").write("a\n")
        g("add", "f")
        g("commit", "-qm", "a")
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
        env = dict(os.environ)
        env["PATH"] = bind + os.pathsep + env["PATH"]
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
        self.assertIn("444", r.stdout)
        self.assertIn("555", r.stdout)

    def test_no_cancel_when_push_did_not_land(self):
        repo, env, cancels, head, old = self._cancel_fixture()
        # rewind the remote-tracking ref so HEAD != remote tip (push failed/rejected)
        import subprocess
        subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", old], cwd=repo)
        r = self._run_env(repo, env, "git push origin dev")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(cancels) and open(cancels).read().strip(),
                         "cancelled a run although the push did not land")

    # --- force-cancel escalation (#24) --------------------------------------
    # A normal `gh run cancel` can have NO visible effect (live incident:
    # restreamer 2026-07-21 -- an old run kept starting new jobs for 50+ min).
    # A 120s SYNCHRONOUS wait inside this hook would slow down every single
    # push, so escalation checks a run cancelled on a PRIOR invocation
    # instead -- never blocking here.

    def _escalation_fixture(self, run_list_json="[]", view_status="in_progress"):
        import subprocess
        import shutil
        import stat
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        repo = os.path.join(root, "repo")
        bind = os.path.join(root, "bin")
        os.makedirs(repo)
        os.makedirs(bind)

        def g(*a):
            return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)
        g("init", "-q", "-b", "dev")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        open(os.path.join(repo, "f"), "w").write("a\n")
        g("add", "f")
        g("commit", "-qm", "a")
        head = g("rev-parse", "HEAD").stdout.strip()
        g("update-ref", "refs/remotes/origin/dev", head)  # simulate a LANDED push

        cancels = os.path.join(root, "cancels")
        force_cancels = os.path.join(root, "force_cancels")
        gh = os.path.join(bind, "gh")
        with open(gh, "w") as fh:
            fh.write(f'''#!/usr/bin/env bash
[ "$1 $2" = "repo view" ] && {{ echo '{{"name":"x"}}'; exit 0; }}
if [ "$1 $2" = "run list" ]; then
  echo '{run_list_json}'
  exit 0
fi
[ "$1 $2" = "run cancel" ] && {{ echo "$3" >> "{cancels}"; exit 0; }}
if [ "$1 $2" = "run view" ]; then
  echo "{view_status}"
  exit 0
fi
if [ "$1" = "api" ]; then
  echo "$2" >> "{force_cancels}"
  exit 0
fi
exit 0
''')
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        env = dict(os.environ)
        env["PATH"] = bind + os.pathsep + env["PATH"]
        git_dir = os.path.join(repo, ".git")
        pending_file = os.path.join(git_dir, "airuleset-pending-cancels.json")
        return repo, env, cancels, force_cancels, pending_file

    def _seed_pending(self, pending_file, rid, age_seconds):
        import time
        with open(pending_file, "w") as f:
            json.dump([[rid, time.time() - age_seconds]], f)

    def test_new_cancel_is_recorded_to_pending_file(self):
        # reuse the ancestor-producing fixture (from the base class) -- its
        # run list has an actual candidate (111) to cancel.
        repo, env, cancels, head, old = self._cancel_fixture()
        pending_file = os.path.join(repo, ".git", "airuleset-pending-cancels.json")
        r = self._run_env(repo, env, "git push origin dev")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(pending_file), "no pending-cancel file written")
        recorded = json.loads(open(pending_file).read())
        self.assertEqual([rid for rid, _ts in recorded], [111], recorded)

    def test_stale_pending_run_escalates_to_force_cancel(self):
        repo, env, cancels, force_cancels, pending_file = self._escalation_fixture(
            view_status="in_progress")
        self._seed_pending(pending_file, 999, age_seconds=200)
        r = self._run_env(repo, env, "git push origin dev")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(force_cancels), "force-cancel API never called")
        calls = open(force_cancels).read()
        self.assertIn("999", calls)
        self.assertIn("force-cancel", calls)
        # one-shot: the entry is dropped from pending afterward
        remaining = json.loads(open(pending_file).read())
        self.assertEqual(remaining, [])

    def test_pending_run_too_recent_is_not_escalated_yet(self):
        repo, env, cancels, force_cancels, pending_file = self._escalation_fixture(
            view_status="in_progress")
        self._seed_pending(pending_file, 999, age_seconds=10)
        r = self._run_env(repo, env, "git push origin dev")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(force_cancels) and open(force_cancels).read(),
                         "escalated a run that is not old enough yet")
        # kept for a LATER invocation, never dropped early
        remaining = json.loads(open(pending_file).read())
        self.assertEqual([rid for rid, _ts in remaining], [999])

    def test_pending_run_already_terminal_is_dropped_without_escalating(self):
        repo, env, cancels, force_cancels, pending_file = self._escalation_fixture(
            view_status="completed")
        self._seed_pending(pending_file, 999, age_seconds=200)
        r = self._run_env(repo, env, "git push origin dev")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(force_cancels) and open(force_cancels).read(),
                         "escalated a run that was already terminal")
        remaining = json.loads(open(pending_file).read())
        self.assertEqual(remaining, [])


class TestPrePushGatesFire(TestCase):
    """pre-push-lint.sh + pre-push-test-check.sh were DEAD ($TOOL_INPUT-only). Lock
    that they FIRE via stdin — a non-push command is a clean no-op, a push payload
    reaches the gate body (not silently skipped by an empty input)."""

    def _run(self, hook, command, cwd):
        import subprocess
        payload = json.dumps({"tool_input": {"command": command}})
        # isolate HOME so pre-push-test-check's audit log ($HOME/devel/airuleset/
        # audits/no-test-skips.log) is written under a temp dir, never the real one
        env = dict(os.environ)
        env["HOME"] = tempfile.mkdtemp()
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / hook)],
                              input=payload, text=True, capture_output=True,
                              cwd=cwd, timeout=60, env=env)

    def test_test_check_blocks_feature_without_test(self):
        # a feature-code change with no test file must block (exit 2) via stdin
        import subprocess
        import shutil
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        def g(*a):
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        open(os.path.join(root, "app.py"), "w").write("def f():\n    return 1\n")
        g("add", "app.py")
        g("commit", "-qm", "base")
        g("branch", "-q", "dev")
        g("checkout", "-q", "dev")
        g("update-ref", "refs/remotes/origin/main", g("rev-parse", "HEAD").stdout.strip())
        open(os.path.join(root, "feature.py"), "w").write("def g():\n    return 2\n")
        g("add", "feature.py")
        g("commit", "-qm", "feat: add g")
        r = self._run("pre-push-test-check.sh", "git push origin dev", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("BLOCKED", r.stdout)

    def test_test_check_no_test_bypass(self):
        import subprocess
        import shutil
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        def g(*a):
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        with open(os.path.join(root, "app.py"), "w") as fh:
            fh.write("x=1\n")
        g("add", "app.py")
        g("commit", "-qm", "base")
        g("branch", "-q", "dev")
        g("checkout", "-q", "dev")
        g("update-ref", "refs/remotes/origin/main", g("rev-parse", "HEAD").stdout.strip())
        with open(os.path.join(root, "feature.py"), "w") as fh:
            fh.write("y=2\n")
        g("add", "feature.py")
        g("commit", "-qm", "config tweak\n\n[no-test: config-only change]")
        r = self._run("pre-push-test-check.sh", "git push origin dev", root)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_test_check_no_test_bypass_multiline_reason(self):
        # (issue #2) a [no-test: ...] reason that wraps onto a second line inside
        # the commit body must still be honored — grep is line-oriented, so
        # without normalizing the message first, the closing "]" on a later line
        # never matches on the SAME line as the opening "[no-test:" and the
        # bypass silently fails, falling through to the strict feature-code gate.
        import subprocess
        import shutil
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        def g(*a):
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        with open(os.path.join(root, "app.py"), "w") as fh:
            fh.write("x=1\n")
        g("add", "app.py")
        g("commit", "-qm", "base")
        g("branch", "-q", "dev")
        g("checkout", "-q", "dev")
        g("update-ref", "refs/remotes/origin/main", g("rev-parse", "HEAD").stdout.strip())
        with open(os.path.join(root, "feature.py"), "w") as fh:
            fh.write("y=2\n")
        g("add", "feature.py")
        g("commit", "-qm",
          "config tweak\n\n[no-test: this reason genuinely wraps\nonto a second line]")
        r = self._run("pre-push-test-check.sh", "git push origin dev", root)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_non_push_is_noop_for_both_gates(self):
        d = tempfile.mkdtemp()
        for hook in ("pre-push-lint.sh", "pre-push-test-check.sh"):
            self.assertEqual(self._run(hook, "ls -la", d).returncode, 0, hook)


class TestPrePushTestCheckBaseRef(TestCase):
    """pre-push-test-check.sh range bug (odoo-erp, 2026-07-18): the diff/commit
    range was ALWAYS origin/<default>..HEAD, so on a multi-branch repo
    (develop→staging→main) every push of ANY branch re-flagged fix commits
    ALREADY MERGED into develop via green-CI PRs — a docs-only push was blocked
    for merged PR #1694's fix commit (odoo-erp e3d34e37 bypass note). The base
    must be the PR TARGET — the integration branch the work merges into
    (origin/develop there) — while a 2-branch repo's dev→main keeps today's
    whole-PR origin/main range (RED→GREEN ordering still spans the whole PR)."""

    def _run(self, command, cwd):
        import subprocess
        payload = json.dumps({"tool_input": {"command": command}})
        env = dict(os.environ)
        env["HOME"] = tempfile.mkdtemp()
        return subprocess.run(
            ["bash", str(airuleset.REPO_DIR / "hooks" / "pre-push-test-check.sh")],
            input=payload, text=True, capture_output=True, cwd=cwd, timeout=60,
            env=env)

    def _mkrepo(self):
        import subprocess
        import shutil
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        def g(*a):
            return subprocess.run(["git", *a], cwd=root, capture_output=True,
                                  text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        open(os.path.join(root, "app.py"), "w").write("def f():\n    return 1\n")
        g("add", "app.py")
        g("commit", "-qm", "base")
        return root, g

    def test_docs_push_of_new_branch_ignores_merged_develop_fixes(self):
        # The odoo-erp repro: develop is AHEAD of origin/main by a MERGED fix
        # commit; a new docs-only branch cut from develop must push clean.
        root, g = self._mkrepo()
        base = g("rev-parse", "HEAD").stdout.strip()
        g("checkout", "-qb", "develop")
        open(os.path.join(root, "app.py"), "a").write("# fixed\n")
        g("add", "app.py")
        g("commit", "-qm", "fix: crash in parser\n\nCloses #1694")
        g("update-ref", "refs/remotes/origin/main", base)
        g("update-ref", "refs/remotes/origin/develop",
          g("rev-parse", "HEAD").stdout.strip())
        g("checkout", "-qb", "docs-tweak")
        open(os.path.join(root, "README.md"), "w").write("# docs\n")
        g("add", "README.md")
        g("commit", "-qm", "docs: tweak readme")
        r = self._run("git push origin docs-tweak", root)
        self.assertEqual(r.returncode, 0,
                         "merged develop history must NOT re-flag: %s" % r.stdout)

    def test_two_branch_dev_keeps_whole_pr_range(self):
        # No develop → dev's PR target stays origin/main: an unmerged fix
        # commit with no test still blocks (gate 2 semantics unchanged).
        root, g = self._mkrepo()
        g("update-ref", "refs/remotes/origin/main",
          g("rev-parse", "HEAD").stdout.strip())
        g("checkout", "-qb", "dev")
        open(os.path.join(root, "app.py"), "a").write("# fixed\n")
        g("add", "app.py")
        g("commit", "-qm", "fix: crash in parser\n\nCloses #7")
        r = self._run("git push origin dev", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("BLOCKED", r.stdout)


class TestPrePushTestCheckItParenFalsePositive(TestCase):
    """#41 (restreamer PR #324, 2026-07-25): Gate 2's inline-test regex had a
    bare `it\\(` alternative with no word boundary, so it matched the
    substring `it(` inside completely unrelated code — `sys.exit(1)`,
    `.split(...)`, `.init()` all contain it right after a word character.
    A commit whose diff happened to add ANY such call flipped
    SEEN_TEST_COMMIT=1, silently defeating Gate 2's RED-before-GREEN check
    for every later bug-fix commit in the same push — a false NEGATIVE,
    harder to notice than a false block. Fixed to `\\bit\\(['\"]` — a word
    boundary (excludes exit(/split(/init() plus a quote as the first arg
    (the real Jest/Mocha `it('desc', ...)` shape)."""

    def _run(self, command, cwd):
        payload = json.dumps({"tool_input": {"command": command}})
        env = dict(os.environ)
        env["HOME"] = tempfile.mkdtemp()
        return subprocess.run(
            ["bash", str(airuleset.REPO_DIR / "hooks" / "pre-push-test-check.sh")],
            input=payload, text=True, capture_output=True, cwd=cwd, timeout=60,
            env=env)

    def _mkrepo(self):
        import shutil
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        def g(*a):
            return subprocess.run(["git", *a], cwd=root, capture_output=True,
                                  text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        open(os.path.join(root, "app.py"), "w").write("def f():\n    return 1\n")
        g("add", "app.py")
        g("commit", "-qm", "base")
        g("update-ref", "refs/remotes/origin/main", g("rev-parse", "HEAD").stdout.strip())
        g("checkout", "-qb", "dev")
        return root, g

    def test_sys_exit_call_does_not_count_as_a_test_commit(self):
        # LIVE shape (restreamer #324): a validator script's `sys.exit(1)`
        # call incidentally flipped SEEN_TEST_COMMIT before it ever saw a
        # real test. Gate 2 must still block the later bug-fix commit.
        root, g = self._mkrepo()
        open(os.path.join(root, "validate.py"), "w").write(
            "import sys\n\ndef validate(ok):\n"
            "    if not ok:\n        sys.exit(1)\n"
        )
        g("add", "validate.py")
        g("commit", "-qm", "chore: add config validator")
        open(os.path.join(root, "app.py"), "a").write("# fixed\n")
        g("add", "app.py")
        g("commit", "-qm", "fix: crash in parser\n\nCloses #41")
        r = self._run("git push origin dev", root)
        self.assertEqual(r.returncode, 2,
                         "sys.exit(1) masked the missing test: " + r.stdout)
        self.assertIn("BLOCKED", r.stdout)

    def test_genuine_js_it_test_commit_still_recognized(self):
        # regression guard: a REAL inline it('...') test (non-test-named
        # path, so only the INLINE recognition can catch it) must still
        # count as the preceding test commit — the tightened regex must not
        # also lose the genuine case.
        root, g = self._mkrepo()
        open(os.path.join(root, "checks.js"), "w").write(
            "it('validates the thing', () => { expect(f(1)).toBe(2); });\n"
        )
        g("add", "checks.js")
        g("commit", "-qm", "add inline coverage")
        open(os.path.join(root, "app.py"), "a").write("# fixed\n")
        g("add", "app.py")
        g("commit", "-qm", "fix: crash in parser\n\nCloses #41")
        r = self._run("git push origin dev", root)
        self.assertEqual(r.returncode, 0,
                         "genuine it(...) test no longer recognized: " + r.stdout)


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
        self._g(repo, "add", "shared")
        self._g(repo, "commit", "-qm", "base")
        self._g(repo, "push", "-q", "origin", "main")
        self._g(repo, "checkout", "-q", "-b", "dev")
        self._g(repo, "push", "-q", "origin", "dev")
        self._g(repo, "remote", "set-head", "origin", "-a")
        return repo

    def _edit_line2(self, repo, branch, text):
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
        # #417: the block reason moved to stderr (Claude Code's PreToolUse
        # exit-2 contract surfaces the block reason FROM stderr).
        self.assertIn("CONFLICT", r.stderr)

    def test_block_reason_reaches_stderr_not_just_stdout(self):
        # #417: Claude Code's PreToolUse exit-2 contract surfaces the block
        # REASON from stderr -- every other exit-2-emitting hook in this repo
        # routes its message through `>&2`; this one used to be the one
        # outlier, landing its whole block message on stdout with ZERO bytes
        # on stderr (reproduced live: exit 2, 596 bytes stdout, 0 bytes
        # stderr against a real genuine-conflict repo), which the harness
        # then reports as the opaque "No stderr output" hook error instead
        # of the real CONFLICT explanation. The pre-existing
        # test_blocks_on_genuine_conflict above never checked stderr at all.
        repo = self._conflicting()
        r = self._run(repo, "git push origin dev")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("CONFLICT", r.stderr,
                       "block reason must be on stderr, not only stdout "
                       f"(stdout={r.stdout!r} stderr={r.stderr!r})")

    def test_allows_merge_commit_only_behind(self):
        # THE #1 critical false-block: after a --no-ff PR merge + version bump, dev
        # is "behind" main by the merge-commit object but has NO content to merge.
        repo = self._base_repo()
        open(os.path.join(repo, "d"), "w").write("devwork\n")
        self._g(repo, "add", "d")
        self._g(repo, "commit", "-qm", "devwork")
        self._g(repo, "push", "-q", "origin", "dev")
        self._g(repo, "checkout", "-q", "main")
        self._g(repo, "merge", "-q", "--no-ff", "dev", "-m", "Merge PR")
        self._g(repo, "push", "-q", "origin", "main")
        self._g(repo, "checkout", "-q", "dev")
        open(os.path.join(repo, "version"), "w").write("v2\n")
        self._g(repo, "add", "version")
        self._g(repo, "commit", "-qm", "bump")
        self._g(repo, "remote", "set-head", "origin", "-a")
        r = self._run(repo, "git push origin dev")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_clean_behind(self):
        # main adds a NEW file dev lacks — behind but a clean merge, no conflict
        repo = self._base_repo()
        self._g(repo, "checkout", "-q", "main")
        open(os.path.join(repo, "newfile"), "w").write("x\n")
        self._g(repo, "add", "newfile")
        self._g(repo, "commit", "-qm", "newfile")
        self._g(repo, "push", "-q", "origin", "main")
        self._g(repo, "checkout", "-q", "dev")
        open(os.path.join(repo, "dd"), "w").write("y\n")
        self._g(repo, "add", "dd")
        self._g(repo, "commit", "-qm", "dwork")
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


class TestDiscordSuppressEmbeds(TestCase):
    """Every notification POST must carry Discord message flags: 4
    (SUPPRESS_EMBEDS). A URL in a notification (the run-card's 🔗 link) must
    never unfurl into a giant link-preview — the codex-bridge card rendered a
    screen-sized Odoo-logo embed under every message (user complaint,
    2026-07-04). Links stay clickable; only the preview is dropped."""

    def test_python_post_sends_suppress_embeds_flag(self):
        import notify
        import unittest.mock as m
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["data"] = json.loads(req.data.decode())
            return m.Mock(read=lambda: b"")

        with m.patch.object(notify.urllib.request, "urlopen", fake_urlopen):
            ok = notify._post_discord("tok", "123", "text s https://example.com")
        self.assertTrue(ok)
        self.assertEqual(captured["data"].get("flags"), notify.SUPPRESS_EMBEDS)
        self.assertEqual(notify.SUPPRESS_EMBEDS, 4)

    def test_shell_send_curls_carry_suppress_embeds(self):
        src = (airuleset.REPO_DIR / "hooks" / "notify-discord-send.sh").read_text()
        posts = [seg for seg in src.split("curl ")
                 if "channels/${CH}/messages" in seg]
        self.assertGreaterEqual(len(posts), 2,
                                "expected the confirm + background POST paths")
        for seg in posts:
            self.assertIn("flags: 4", seg,
                          "a POST path is missing SUPPRESS_EMBEDS (flags: 4)")


class TestRecordQuestionCLI(TestCase):
    """`airuleset.py notify --record-question` persists the ❓ ping's Discord
    message id → asking session, so the watchdog can route the user's reply back.
    The shell send path calls this on a confirmed ❓ POST."""

    def test_record_question_writes_map(self):
        # Real subprocess: point HOME at a tmp dir so notify writes the map under
        # <tmp>/.claude/ (the CLI resolves ~/.claude, not an in-process patch).
        with tempfile.TemporaryDirectory() as home:
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "notify", "--record-question", "--message-id", "424242424242",
                 "--channel", "900900900900", "--session", "sid-xyz",
                 "--cwd", "/home/x/proj"],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home, "PYTHONPATH": str(airuleset.REPO_DIR)})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "recorded")
            qp = Path(home) / ".claude" / "discord-questions.json"
            data = json.loads(qp.read_text())
            self.assertEqual(data["424242424242"]["session"], "sid-xyz")
            self.assertEqual(data["424242424242"]["channel"], "900900900900")


class TestSendPathRecordsQuestion(TestCase):
    """The ❓ confirm-send path passes ND_SESSION_ID and records the message id;
    the send script captures the POST body's `.id` and calls --record-question."""

    def test_send_script_wires_record_question(self):
        src = (airuleset.REPO_DIR / "hooks" / "notify-discord-send.sh").read_text()
        self.assertIn("--record-question", src)
        self.assertIn("ND_SESSION_ID", src)
        self.assertIn(".id // empty", src)      # extracts the created message id

    def test_pending_hook_passes_session_id(self):
        src = (airuleset.REPO_DIR / "hooks" / "notify-discord-pending.sh").read_text()
        self.assertIn("ND_SESSION_ID=", src)


class TestQuestionQualityGate(TestCase):
    """stop-check-question-quality.sh — HARD gate on the SHAPE of every ❓ turn.

    The user's complaint (2026-07-05, after the block-delivery fix): questions
    STILL arrive without an intro ('Po zmazaní hneď overím…' — deleting WHAT?)
    and one ping crammed THREE decisions ('odpovedz na ktorékoľvek z 3') which
    is unanswerable over the Discord-reply routing (the reply is typed back
    into the session — nobody knows which of the 3 it answers). Rules alone
    did not change session behavior → hook enforcement:
      (a) the delivered question block MUST contain the briefing line
          '**Otázka — projekt …:**' (the úvod),
      (b) ONE ping = ONE decision (no enumerated multi-question piles)."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-question-quality.sh"
    _counter = 0

    TEMPLATE_Q = ("**Otázka — projekt restreamer (nahrávanie kostolných "
                  "prenosov):** disk na nahrávacom počítači je takmer plný, "
                  "preto treba zmazať staré nahrávky, inak zlyhá najbližší "
                  "prenos.\n"
                  "• Zmazať staršie ako 3 dni (odporúčam) — uvoľní ~40 GB\n"
                  "• Zmazať všetky — uvoľní najviac, prídeš o archív\n"
                  "❓ NEEDS YOU: zmazať nahrávky staršie ako 3 dni?")

    def _sid(self):
        TestQuestionQualityGate._counter += 1
        sid = f"test-qq-{os.getpid()}-{TestQuestionQualityGate._counter}"
        self.addCleanup(
            lambda: os.path.exists(f"/tmp/airuleset-question-quality-block-{sid}")
            and os.remove(f"/tmp/airuleset-question-quality-block-{sid}")
        )
        return sid

    def _run(self, msg, sid=None):
        sid = sid or self._sid()
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              text=True, capture_output=True), sid

    def _blocked(self, r):
        return r.returncode == 0 and '"block"' in r.stdout

    def _clean(self, r):
        return r.returncode == 0 and r.stdout.strip() == ""

    def test_bare_short_question_without_briefing_blocked(self):
        r, _ = self._run("❓ NEEDS YOU: schváliš merge PR #5?")
        self.assertTrue(self._blocked(r), r.stdout)
        self.assertIn("Otázka — projekt", r.stdout)   # the reason TEACHES the shape

    def test_context_free_restreamer_shape_blocked(self):
        # the live 2026-07-05 ping: a context sentence that explains nothing
        # ('Po zmazaní hneď overím…' — deleting WHAT?) + the bare decision line
        r, _ = self._run(
            "Po zmazaní hneď overím voľné miesto a nič ďalšie nereštartujem "
            "(dev oprava ostáva nezmergovaná).\n\n"
            "❓ NEEDS YOU: ktorú možnosť — staršie ako 3 dni (odporúčam), "
            "všetky nahrávky, alebo iný dátum?")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_structured_template_question_allowed(self):
        r, _ = self._run("pracovný text vyššie\n\n" + self.TEMPLATE_Q)
        self.assertTrue(self._clean(r), r.stdout)

    def test_template_in_paragraph_above_bare_marker_allowed(self):
        # delivery pulls the ONE paragraph above a short bare marker — the
        # gate must accept the same shape it delivers
        head, marker = self.TEMPLATE_Q.rsplit("\n", 1)
        r, _ = self._run(head + "\n\n" + marker)
        self.assertTrue(self._clean(r), r.stdout)

    def test_multi_question_pile_blocked(self):
        # the live codex-bridge 3-in-1 ping — unanswerable via Discord reply
        r, _ = self._run(
            "**Otázka — projekt codex-bridge (prenos dát z Codexu do Odoo):** "
            "tri tickety čakajú na tvoje rozhodnutie.\n"
            "❓ NEEDS YOU: Odpovedz na ktorékoľvek z 3, aj postupne — "
            "(1) #250 vratné obaly: vytvoriť jednoduché produkty (odporúčam) "
            "alebo nechať tak? (2) #232 alergény: odkiaľ vziať zdroj? "
            "(3) #253 cena výroby: uložené číslo alebo počítaný rozpad?")
        self.assertTrue(self._blocked(r), r.stdout)
        self.assertIn("ONE decision", r.stdout)

    def test_enumerated_two_questions_blocked(self):
        r, _ = self._run(
            "**Otázka — projekt demo (ukážka):** dve veci naraz.\n"
            "❓ NEEDS YOU: (1) zmazať zálohu? (2) reštartnúť službu?")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_enumerated_steps_in_briefing_with_one_question_allowed(self):
        # (1)/(2) describing STEPS with a single final decision is fine —
        # the pile heuristic requires ≥2 question marks alongside (1)+(2)
        r, _ = self._run(
            "**Otázka — projekt demo (ukážka):** postup bude (1) zmažem "
            "staré nahrávky, (2) overím voľné miesto — nič sa nereštartuje.\n"
            "• Áno, uvoľni disk (odporúčam) — bez rizika\n"
            "• Nie — disk nechám ako je\n"
            "❓ NEEDS YOU: môžem takto uvoľniť disk?")
        self.assertTrue(self._clean(r), r.stdout)

    def test_asked_form_without_briefing_blocked(self):
        r, _ = self._run("❓ ASKED: reset na 0 dB alebo posledný preset?\n\n"
                         "⏳ WORKING: robím #59")
        self.assertTrue(self._blocked(r), r.stdout)

    def test_asked_form_with_briefing_allowed(self):
        r, _ = self._run(
            "**Otázka — projekt iem (mixovanie zvuku v kostole):** ticket #58 "
            "mení správanie tlačidla reset a treba vybrať predvolenú hodnotu.\n"
            "• 0 dB (odporúčam) — štandardný stav\n"
            "• Posledný preset — pokračuje kde skončil\n"
            "❓ ASKED: reset na 0 dB (odporúčam) alebo posledný preset?\n\n"
            "⏳ WORKING: medzitým robím #59 (nezávislé od odpovede)")
        self.assertTrue(self._clean(r), r.stdout)

    def test_briefing_wall_of_text_blocked(self):
        # live 2026-07-05 (camera-box): ~700 chars of thread/lock jargon as
        # the intro — a wall, not "štruktúrované a ľahko čitateľné". Úvod =
        # 2–4 SHORT sentences (~400 chars max); details belong in the ticket.
        long_brief = ("**Otázka — projekt camera-box (ovládanie kamier + OBS):** "
                      + "veľmi dlhé technické vysvetlenie o vláknach a zámkoch. " * 14)
        r, _ = self._run(long_brief.rstrip()
                         + "\n• Zavrieť #508 ako splnené (odporúčam) — nič netreba\n"
                           "• Odmerať tvoj scenár — povedz aký\n"
                           "❓ NEEDS YOU: zavrieť #508 ako splnené?")
        self.assertTrue(self._blocked(r), r.stdout)
        self.assertIn("2–4", r.stdout)      # the reason teaches the cap

    def test_good_briefing_with_bullet_options_not_false_positived(self):
        # LIVE regression (camera-box, 2026-07-05 "velke zhorsenie"): mawk
        # treats regex brackets as BYTE classes, so `[•-]` never matched the
        # multi-byte `•` option lines — they got counted INTO the briefing and
        # this perfectly good ~280-char question looped block→rewrite→block.
        # Briefing under the cap + `•` options + total over the cap = ALLOWED.
        r, _ = self._run(
            "Otázka — projekt camera-box (imag-nb strihací počítač, kostolný "
            "live): Program na HDMI beží 60fps. Ostáva vrátiť notebook display "
            "(tam ide multiview) a docky. Zapnutie notebook displaya je zmena "
            "grafiky, ktorá pred live môže v horšom prípade zhodiť celý "
            "session aj s projekciou.\n"
            "• Prevezmi to ty (odporúčam) — myš aj touchpad fungujú, otvoríš "
            "si to ako zvyčajne; ja spravím presne to, čo mi ukážeš, bez "
            "rizika a bez ďalšieho babrania sa v grafike pred prenosom\n"
            "• Zapnem notebook display ja — opatrne, viem to vrátiť, ale malé "
            "riziko glitchu X pred live, ktorý by mohol zhodiť projekciu\n"
            "❓ NEEDS YOU: doklikáš si to ty, alebo mám zapnúť notebook "
            "display ja?")
        self.assertTrue(self._clean(r),
                        "options must terminate the briefing count: " + r.stdout)

    def test_options_bullets_required(self):
        # "ziadne odrazky" — options must be bullet lines, not prose
        r, _ = self._run(
            "**Otázka — projekt demo (ukážka):** krátky úvod čo sa deje a prečo.\n"
            "❓ NEEDS YOU: zmazať starú zálohu?")
        self.assertTrue(self._blocked(r), r.stdout)
        self.assertIn("odrážk", r.stdout)   # the reason teaches bullets

    def test_non_question_turns_pass(self):
        for msg in ["✅ DONE: nasadené v1.2.3, CI zelené",
                    "⏳ WORKING: CI beží — nič odo mňa nepotrebuješ",
                    "len bežný text bez markera",
                    "✅ DONE: odpoveď na Discord ❓ ping sa doručí správne"]:
            r, _ = self._run(msg)
            self.assertTrue(self._clean(r), f"{msg!r}: {r.stdout}")

    def test_verbatim_repeat_of_pinged_question_bypasses_shape_gate(self):
        # camera-box 2026-07-05 chat wall: the /goal evaluator re-poked a
        # session BLOCKED on the user every ~40s and each reply re-printed the
        # whole question block. The mandated re-poke reply is ONE line — the
        # previous ❓ line VERBATIM — so the gate must recognize the repeat of
        # an ALREADY-DELIVERED question (LASTQ dedup key matches the marker
        # line) and skip the shape checks for it.
        sid = self._sid()
        q = "spraviť zálohu pred migráciou?"
        lastq = f"/tmp/claude-discord-lastq-{sid}"
        with open(lastq, "w") as fh:
            fh.write(q)
        self.addCleanup(lambda: os.path.exists(lastq) and os.remove(lastq))
        r, _ = self._run("❓ NEEDS YOU: " + q, sid=sid)
        self.assertTrue(self._clean(r),
                        "verbatim repeat of a pinged question must pass: "
                        + r.stdout)

    def test_different_question_still_shape_gated_despite_lastq(self):
        # only the IDENTICAL repeat bypasses — a different bare question is a
        # NEW ask and must carry the full template
        sid = self._sid()
        lastq = f"/tmp/claude-discord-lastq-{sid}"
        with open(lastq, "w") as fh:
            fh.write("spraviť zálohu pred migráciou?")
        self.addCleanup(lambda: os.path.exists(lastq) and os.remove(lastq))
        r, _ = self._run("❓ NEEDS YOU: zmazať staré nahrávky?", sid=sid)
        self.assertTrue(self._blocked(r), r.stdout)

    def _touch_active(self, sid, age=0):
        f = f"/tmp/claude-user-active-{sid}"
        with open(f, "w") as fh:
            fh.write("")
        if age:
            old = time.time() - age
            os.utime(f, (old, old))
        self.addCleanup(lambda: os.path.exists(f) and os.remove(f))
        return f

    def test_present_user_skips_shape_enforcement(self):
        # "Hruza!!!" (camera-box, 2026-07-05): the gate demanded phone-shape
        # templates MID-DIALOG while the user was sitting at the terminal
        # actively typing — every rejection re-printed the question + a huge
        # hook error into their chat. The template protects the AWAY user's
        # phone ping; a PRESENT user (real prompt within ~10 min) is in a
        # conversation → no shape gating.
        sid = self._sid()
        self._touch_active(sid)
        r, _ = self._run("❓ NEEDS YOU: nechať 2 OBS inštancie, alebo spojiť "
                         "do jednej cez rebuild?", sid=sid)
        self.assertTrue(self._clean(r),
                        "present user must not be shape-gated: " + r.stdout)

    def test_away_user_still_fully_gated(self):
        # the presence marker is stale (>10 min) → the user walked away → the
        # phone-shape enforcement is back in full
        sid = self._sid()
        self._touch_active(sid, age=700)
        r, _ = self._run("❓ NEEDS YOU: schváliš merge PR #5?", sid=sid)
        self.assertTrue(self._blocked(r), r.stdout)

    def test_numbered_options_satisfy_bullets_check(self):
        r, _ = self._run(
            "**Otázka — projekt demo (ukážka):** krátky úvod čo sa deje a prečo "
            "sa pýtam.\n"
            "1. Prvá možnosť (odporúčam) — rýchla\n"
            "2. Druhá možnosť — pomalšia\n"
            "❓ NEEDS YOU: prvá alebo druhá?")
        self.assertTrue(self._clean(r), r.stdout)

    def test_retry_cap_lets_message_through(self):
        sid = self._sid()
        bad = "❓ NEEDS YOU: schváliš merge?"
        for _ in range(3):
            r, _ = self._run(bad, sid=sid)
            self.assertTrue(self._blocked(r), r.stdout)
        r, _ = self._run(bad, sid=sid)
        self.assertTrue(self._clean(r), "retry cap must stop an infinite loop")


class TestAutopilotGoalStop(TestCase):
    """The /goal line must make BLOCKED-ON-USER an explicit transcript-provable
    STOP condition — the evaluator kept continuing a ❓-blocked session every
    ~40s and each re-poke re-printed the whole question into the chat
    (camera-box, 2026-07-05)."""

    def _skill(self):
        return (airuleset.REPO_DIR / "skills" / "autopilot" / "SKILL.md").read_text()

    def test_goal_line_stops_on_unanswered_needs_you(self):
        s = self._skill()
        self.assertIn("ends with a line starting `❓ NEEDS YOU:` and there is "
                      "NO user message after it", s)
        self.assertIn("NEVER continue me past an unanswered", s)

    def test_goal_line_rearm_after_answer(self):
        self.assertIn("re-prints this /goal line", self._skill())

    def test_repoke_reply_is_one_verbatim_line(self):
        s = self._skill()
        self.assertIn("EXACTLY ONE LINE", s)
        self.assertIn("no re-printed", s)      # (wraps: "…question block")


class TestEditQuestionCLI(TestCase):
    def test_no_recent_question_exits_2(self):
        home = tempfile.mkdtemp()
        r = subprocess.run(
            [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
             "notify", "--edit-question", "--session", "sess-x"],
            input="nový text otázky", text=True, capture_output=True,
            env={**os.environ, "HOME": home})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no-recent-question", r.stdout)


class TestHookScriptsExist(TestCase):
    def test_hook_scripts_exist(self):
        for script in [
            "session-start-fetch.sh",
            "block-sensitive-staging.sh",
            "pre-deploy-clean-tree.sh",
            "stop-check-untracked-work.sh",
            "stop-check-status-marker.sh",
            "stop-check-question-quality.sh",
            "stop-check-prod-gating.sh",
            "stop-check-sendmessage-narration.sh",
            "notify-discord-pending.sh",
            "notify-discord.sh",
            "clear-question-dedup.sh",
            "pre-push-base-sync.sh",
            "post-push-ci-cleanup.sh",
            "pre-push-lint.sh",
            "pre-push-test-check.sh",
            "block-test-skips.sh",
            "block-history-rewrite.sh",
            "block-destructive-remote.sh",
            "pre-write-script-check.sh",
        ]:
            path = airuleset.REPO_DIR / "hooks" / script
            self.assertTrue(path.exists(), f"Missing hook: {path}")
            self.assertTrue(os.access(path, os.X_OK), f"Not executable: {path}")


class TestSessionStartFetchHook(TestCase):
    """hooks/session-start-fetch.sh (#314) — the SessionStart hook used to only
    `git fetch` (updates the invisible origin/<branch> ref) and print a
    WARNING when behind, never actually moving the local branch/working
    tree. A long-lived, mostly-passive session (a sub-dev stream account's
    persistent tmux, cwd = a project checkout) reads its CLAUDE.md straight
    off that tree at boot — if the tree never advances, that content can
    sit weeks stale even though the hook "fetched" every single session.

    These tests lock the fix: fast-forward the local branch to
    origin/<branch> automatically, but ONLY when it is provably safe (clean
    tree, real branch, no in-progress git operation, and HEAD is a genuine
    ancestor of origin/<branch> — never a reset/checkout -f/history rewrite,
    only `git merge --ff-only`)."""

    HOOK = airuleset.REPO_DIR / "hooks" / "session-start-fetch.sh"

    def _g(self, cwd, *args):
        import subprocess
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

    def _base_repo(self):
        """Remote (bare) + a clone on 'main', origin/HEAD set, one commit."""
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
        open(os.path.join(repo, "f"), "w").write("v1\n")
        self._g(repo, "add", "f")
        self._g(repo, "commit", "-qm", "init")
        self._g(repo, "push", "-q", "origin", "main")
        self._g(repo, "remote", "set-head", "origin", "-a")
        return repo

    def _advance_origin(self, repo, text="v2\n"):
        """Push one more commit to origin/main from a SEPARATE clone, so the
        local `repo` checkout falls behind without touching its own tree.
        `--branch main` forces checkout of `main` explicitly — the bare
        remote's own HEAD symref defaults to whatever `init.defaultBranch`
        is (often unrelated to "main"), so relying on the default checkout
        is not reliable across git configs."""
        import shutil
        parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        other = os.path.join(parent, "other")
        origin_url = self._g(repo, "remote", "get-url", "origin").stdout.strip()
        clone = self._g(parent, "clone", "-q", "--branch", "main", origin_url, other)
        assert clone.returncode == 0, clone.stderr
        self._g(other, "config", "user.email", "t@t")
        self._g(other, "config", "user.name", "t")
        open(os.path.join(other, "f"), "w").write(text)
        commit = self._g(other, "commit", "-qam", "advance")
        assert commit.returncode == 0, commit.stderr
        push = self._g(other, "push", "-q", "origin", "main")
        assert push.returncode == 0, push.stderr

    def _run(self, repo):
        import subprocess
        return subprocess.run(["bash", str(self.HOOK)], cwd=repo,
                              capture_output=True, text=True, timeout=30)

    def test_clean_behind_fast_forwards(self):
        repo = self._base_repo()
        self._advance_origin(repo)
        before = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        r = self._run(repo)
        after = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        origin_head = self._g(repo, "rev-parse", "origin/main").stdout.strip()
        self.assertNotEqual(before, after, r.stdout + r.stderr)
        self.assertEqual(after, origin_head, "local HEAD must equal origin/main")
        self.assertEqual(
            open(os.path.join(repo, "f")).read(), "v2\n",
            "working tree content must reflect the fast-forwarded commit")

    def test_up_to_date_no_op(self):
        repo = self._base_repo()
        before = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        r = self._run(repo)
        after = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(before, after)
        self.assertNotIn("WARNING", r.stdout)

    def test_dirty_tree_not_fast_forwarded(self):
        # #314 adversarial-review F2: the ORIGINAL version of this test dirtied
        # the SAME file ("f") the incoming commit also touches — so with the
        # dirty-tree guard REMOVED entirely, `git merge --ff-only` still
        # refuses on its own (local changes would be overwritten by the
        # incoming commit), and the test passed for the WRONG reason (a
        # mutant that drops the dirty-tree check survives this test
        # unchanged). Dirty an UNRELATED tracked file the incoming commit
        # never touches: WITHOUT the guard, `--ff-only` genuinely succeeds
        # (an unrelated uncommitted change does not block a real
        # fast-forward), so this version can actually distinguish "the
        # guard ran" from "the merge would have failed anyway".
        repo = self._base_repo()
        open(os.path.join(repo, "other"), "w").write("tracked\n")
        self._g(repo, "add", "other")
        self._g(repo, "commit", "-qm", "add other")
        self._g(repo, "push", "-q", "origin", "main")
        self._advance_origin(repo)
        # local uncommitted edit to an UNRELATED path — must never be
        # touched by the hook, and must be what actually blocks it (not a
        # coincidental merge conflict on the file origin also changed)
        open(os.path.join(repo, "other"), "w").write("LOCAL UNCOMMITTED\n")
        before = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        r = self._run(repo)
        after = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(before, after, "dirty tree must never be fast-forwarded")
        self.assertEqual(open(os.path.join(repo, "other")).read(), "LOCAL UNCOMMITTED\n")
        self.assertIn("working tree dirty", r.stdout + r.stderr)

    def test_diverged_branch_not_fast_forwarded(self):
        repo = self._base_repo()
        self._advance_origin(repo)
        # a genuine local commit that conflicts with origin's advance —
        # HEAD is NOT an ancestor of origin/main anymore
        open(os.path.join(repo, "f"), "w").write("LOCAL DIVERGED\n")
        self._g(repo, "commit", "-qam", "local divergent commit")
        before = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        r = self._run(repo)
        after = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(before, after, "diverged branch must never be fast-forwarded")
        # #314 adversarial review F2: divergence always defeats `--ff-only`
        # on its own (real HEAD movement can never discriminate "the
        # ancestor guard ran" from "the merge just failed anyway"), so this
        # asserts the guard's OWN wording specifically — a mutant that
        # drops the `merge-base --is-ancestor` check entirely would fall
        # through to the generic "fast-forward attempt failed" message
        # instead, which this assertion catches.
        self.assertIn("has diverged", r.stdout + r.stderr)

    def test_mid_revert_not_fast_forwarded(self):
        # #314 adversarial review F3: REVERT_HEAD is CHERRY_PICK_HEAD's exact
        # twin for `git revert` — a clean-tree mid-revert (tree restored to
        # clean after `revert --no-commit`, REVERT_HEAD still present) is
        # reachable and must be refused exactly like mid-cherry-pick/rebase.
        repo = self._base_repo()
        # a second commit to revert against
        open(os.path.join(repo, "f"), "w").write("v2\n")
        self._g(repo, "commit", "-qam", "second commit")
        self._g(repo, "push", "-q", "origin", "main")
        self._advance_origin(repo, text="v3\n")
        self._g(repo, "fetch", "-q", "origin")
        # start a revert of the second commit, then restore the tree to
        # clean while leaving REVERT_HEAD behind — no --no-edit needed
        # since --no-commit never opens an editor
        self._g(repo, "revert", "--no-commit", "HEAD")
        self._g(repo, "restore", "--source=HEAD", "--staged", "--worktree", ".")
        git_dir = self._g(repo, "rev-parse", "--git-dir").stdout.strip()
        git_dir = git_dir if os.path.isabs(git_dir) else os.path.join(repo, git_dir)
        self.assertTrue(
            os.path.exists(os.path.join(git_dir, "REVERT_HEAD")),
            "test setup must actually produce a mid-revert state")
        before = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        r = self._run(repo)
        after = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(before, after, "mid-revert repo must never be touched")
        self.assertIn("WARNING", r.stdout + r.stderr)
        self._g(repo, "revert", "--abort")

    def test_ignored_file_collision_not_fast_forwarded(self):
        # #314 adversarial review F1 (CRITICAL, reproduced live): the
        # dirty-tree check is BLIND to ignored files (`git status
        # --porcelain` never lists them), and `--ff-only` treats an
        # ignored/untracked file as expendable relative to an incoming
        # tracked file at the SAME path — so without this guard, a local
        # gitignored file (a real secret, a scratch note) is silently
        # OVERWRITTEN the moment origin adds a tracked file of that name.
        repo = self._base_repo()
        open(os.path.join(repo, ".gitignore"), "w").write("secret.env\n")
        self._g(repo, "add", ".gitignore")
        self._g(repo, "commit", "-qm", "add gitignore")
        self._g(repo, "push", "-q", "origin", "main")
        # a real local file matching the ignored pattern — never committed
        open(os.path.join(repo, "secret.env"), "w").write("MY PRECIOUS LOCAL SECRET\n")
        # origin independently adds a TRACKED file at the same path
        import shutil
        parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        other = os.path.join(parent, "other")
        origin_url = self._g(repo, "remote", "get-url", "origin").stdout.strip()
        self._g(parent, "clone", "-q", "--branch", "main", origin_url, other)
        self._g(other, "config", "user.email", "t@t")
        self._g(other, "config", "user.name", "t")
        open(os.path.join(other, "secret.env"), "w").write("ORIGIN VERSION\n")
        self._g(other, "add", "-f", "secret.env")
        self._g(other, "commit", "-qm", "origin adds secret.env")
        self._g(other, "push", "-q", "origin", "main")
        before = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        r = self._run(repo)
        after = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(before, after, "must never fast-forward past a local/origin path collision")
        self.assertEqual(
            open(os.path.join(repo, "secret.env")).read(), "MY PRECIOUS LOCAL SECRET\n",
            "the local gitignored file's content must survive untouched")
        self.assertIn("WARNING", r.stdout + r.stderr)

    def test_unmeasurable_status_refuses_rather_than_guesses_clean(self):
        # #314 adversarial review F4: a failing `git status` (e.g. an
        # unreadable .git/index) must never silently read as "clean" — an
        # unmeasurable state is treated the same as dirty, never guessed.
        #
        # An unreadable .git/index makes EVERY git command that touches it
        # fail with rc=128, including the hook's OWN internal `git fetch`
        # (verified live) — so `origin/$BRANCH` must already be fetched to
        # its final position BEFORE the chmod, or the hook's BEHIND
        # computation reads a stale (not-yet-advanced) tracking ref and
        # exits early as "up to date" before ever reaching the status
        # check this test is about. `git rev-list --count` itself does not
        # touch the index (verified live), so BEHIND still resolves
        # correctly once fetched.
        repo = self._base_repo()
        self._advance_origin(repo)
        self._g(repo, "fetch", "-q", "origin")
        index_path = os.path.join(repo, ".git", "index")
        os.chmod(index_path, 0o000)
        self.addCleanup(os.chmod, index_path, 0o644)
        before = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        r = self._run(repo)
        after = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(before, after, "an unmeasurable working-tree state must never be fast-forwarded")
        self.assertIn("could not determine working tree state", r.stdout + r.stderr)

    def test_mid_rebase_not_fast_forwarded(self):
        repo = self._base_repo()
        self._advance_origin(repo)
        open(os.path.join(repo, "f"), "w").write("REBASE CONFLICT\n")
        self._g(repo, "commit", "-qam", "local commit that will conflict")
        # local origin/main tracking ref is stale until an explicit fetch —
        # without this, `rebase origin/main` below is a silent no-op
        self._g(repo, "fetch", "-q", "origin")
        # start a rebase onto origin/main that WILL conflict, leaving the
        # repo mid-rebase (rebase-merge/ or rebase-apply/ present)
        self._g(repo, "rebase", "origin/main")
        git_dir = self._g(repo, "rev-parse", "--git-dir").stdout.strip()
        git_dir = git_dir if os.path.isabs(git_dir) else os.path.join(repo, git_dir)
        mid_rebase = (os.path.exists(os.path.join(git_dir, "rebase-merge"))
                      or os.path.exists(os.path.join(git_dir, "rebase-apply")))
        self.assertTrue(mid_rebase, "test setup must actually produce a mid-rebase state")
        before = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        r = self._run(repo)
        after = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(before, after, "mid-rebase repo must never be touched")
        self.assertIn("WARNING", r.stdout + r.stderr)
        # cleanup so tempdir removal doesn't choke on rebase state
        self._g(repo, "rebase", "--abort")

    def test_detached_head_not_fast_forwarded(self):
        repo = self._base_repo()
        first = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        self._advance_origin(repo)
        self._g(repo, "checkout", "-q", first)
        r = self._run(repo)
        after = self._g(repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(first, after, "detached HEAD must never be moved")
        self.assertEqual(r.returncode, 0)


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

    # Plugin-provided agents (REAL since caveman 0d95a81d ships cavecrew-*):
    # `<plugin>:<agent>` is VALID iff the plugin cache carries agents/<agent>.md
    # (either layout: <hash>/agents/ or <hash>/src/agents/ — layout-rot lesson).
    def _plugin_home(self, layout="agents"):
        home = self._tmp_home()
        d = home / ".claude" / "plugins" / "cache" / "mkt" / "plug" / "abc123"
        if layout == "src":
            d = d / "src"
        (d / "agents").mkdir(parents=True)
        (d / "agents" / "myagent.md").write_text("---\nname: myagent\n---\nbody")
        return home

    def test_plugin_cache_agent_allowed(self):
        r = self._run("plug:myagent", home=self._plugin_home())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plugin_cache_agent_src_layout_allowed(self):
        r = self._run("plug:myagent", home=self._plugin_home(layout="src"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plugin_prefixed_without_cache_still_blocked(self):
        r = self._run("plug:ghost", home=self._plugin_home())
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


class TestProseViolationsPrlessCompletion(TestCase):
    """PR-LESS ticket completions must ALSO trigger the full-template gate.

    Incident (david@gk, 2026-07-10/11): the completion-report enforcement fired only
    when the message carried a GitHub PR URL — but a fork-no-merge stream NEVER has a
    PR, so its bare '✅ DONE: #1400 a #1408 hotové' one-liners sailed through and the
    user never saw a proper Work Complete report on that box ("nikdy tam nevidim
    normalne work complete reporty"). A ✅ DONE line naming ticket(s) #N with
    done-vocab (SK/EN), or a READY-FOR-REVIEW hand-off, IS a completion — heading +
    audits + Goal/What changed are required exactly as in the merge flow."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"

    def _sid(self):
        sid = f"test-prless-{uuid.uuid4().hex[:12]}"
        self.addCleanup(lambda: Path(f"/tmp/airuleset-stop-block-{sid}").unlink(missing_ok=True))
        return sid

    def _run(self, msg):
        payload = json.dumps({"last_assistant_message": msg, "session_id": self._sid()})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True)

    def test_bare_slovak_ticket_done_blocked(self):
        # david's literal failure message — no PR URL, no heading, no audits.
        r = self._run("Už hotové — presne toto som spravil v predošlom ťahu.\n\n"
                      "✅ DONE: #1400 a #1408 hotové, ako v poslednom súhrne.")
        self.assertEqual(r.returncode, 0)
        self.assertIn('"block"', r.stdout)

    def test_bare_handoff_done_blocked(self):
        r = self._run("Vetva pushnutá, testy zelené, komentár poslaný.\n\n"
                      "✅ DONE: #1393 odovzdané — READY-FOR-REVIEW komentár na tickete.")
        self.assertEqual(r.returncode, 0)
        self.assertIn('"block"', r.stdout)

    def test_full_fork_shaped_report_without_pr_is_clean(self):
        # The fork-no-merge Work Complete shape: audits + hand-off lines, NO PR/merge.
        msg = ("## ✅ Work Complete\n\n"
               "**Audits & deploy:**\n"
               "✅ /plan-check: 3/3 fulfilled\n"
               "✅ /review: clean — 0 🔴 0 🟡 0 🔵\n"
               "✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵\n"
               "✅ Lokálne overenie: testy + lint zelené (fork vetva david/kiosk)\n"
               "✅ Hand-off: READY-FOR-REVIEW komentár na #1393 (dochádzkový kiosk) + karta\n"
               # The '✅ Výstup:' content-verification line became MANDATORY for
               # every completion report (montalu3 0 € email incident) — a
               # "genuinely clean" report now carries it by definition.
               "✅ Výstup: kiosk obrazovka zobrazuje meno zamestnanca a čas 07:45\n\n"
               "---\n\n"
               "**Goal:** Dochádzkový kiosk pre výrobu.\n"
               "**What changed:** Kiosk beží na erp-test-david, odovzdané gatekeeperovi.\n\n"
               "✅ DONE: #1393 (kiosk) odovzdané na review, nič ďalšie nečaká.")
        r = self._run(msg)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn('"block"', r.stdout, r.stderr)

    def test_conversational_done_about_a_ticket_is_clean(self):
        # Answering a question ABOUT a ticket is not a completion — no done-vocab.
        r = self._run("Ten ticket rieši mapovanie skladov, detaily som vysvetlil vyššie.\n\n"
                      "✅ DONE: odpovedané na tvoju otázku o #123 (mapovanie skladov).")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn('"block"', r.stdout, r.stderr)

    def test_midloop_working_marker_stays_clean(self):
        r = self._run("Ticket #1400 (kiosk oprava) hotový, pokračujem na #1408.\n\n"
                      "⏳ WORKING: ďalší ticket v behu — nič odo mňa netreba.")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn('"block"', r.stdout, r.stderr)


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
    """apply_managed_settings_defaults sets the persistent effortLevel=xhigh
    default in every managed project, preserving all other settings keys,
    idempotently.

    User directive 2026-08-13 ("by default vzdy ultracode... maximalna
    akceleracia"): ultracode is the STANDING fleet default. `effortLevel`
    accepts only low|medium|high|xhigh (docs: `max`/`ultracode` are
    session-only), so managed ultracode is composed of its two real parts:
    `MANAGED_EFFORT_LEVEL = "xhigh"` (this key) + the launch script's
    `--settings '{"ultracode":true}'` flag in every mode except `plain`
    (TestUltracodeLauncher). This deliberately reverses #56's high baseline
    and #53's session-only opt-in — on the user's explicit dated directive,
    not a drift."""

    def test_sets_effort_xhigh(self):
        out = airuleset.apply_managed_settings_defaults({})
        self.assertEqual(out["effortLevel"], "xhigh")

    def test_managed_effort_level_constant_is_xhigh(self):
        # the settings-representable half of the standing ultracode default
        # (the orchestration half is the launch flag, locked separately).
        self.assertEqual(airuleset.MANAGED_EFFORT_LEVEL, "xhigh")

    def test_disables_agent_view(self):
        # Hard-disables the `claude agents` / fleet / `claude --bg` background daemon
        # (detached sessions that survive /exit). Must be a managed default on every
        # install so a fresh machine never spawns unmanaged background Claude.
        out = airuleset.apply_managed_settings_defaults({})
        self.assertIs(out["disableAgentView"], True)

    def test_disables_remote_control(self):
        # User directive 2026-08-13 (#439): "vypni vsade rc remote control aj v
        # nastaveniach claude, vadi mi to" — the statusline showed
        # `/rc connecting…` / `/rc failed` on every session because
        # `remoteControlAtStartup` was persisted true, so every session tried
        # an RC connection at startup. Same unconditional managed-default
        # treatment as disableAgentView/tui/model: a managed box always gets
        # both keys on the next install, regardless of what was there before.
        out = airuleset.apply_managed_settings_defaults({})
        self.assertIs(out["disableRemoteControl"], True)
        self.assertIs(out["remoteControlAtStartup"], False)

    def test_overrides_existing_remote_control_values(self):
        # Unconditional, like disableAgentView/tui/model — a managed box
        # always gets the managed default on the next install, even if a
        # hand-edited settings.json (or a stale pre-#439 install) left the
        # opposite value in place.
        out = airuleset.apply_managed_settings_defaults(
            {"disableRemoteControl": False, "remoteControlAtStartup": True})
        self.assertIs(out["disableRemoteControl"], True)
        self.assertIs(out["remoteControlAtStartup"], False)

    def test_forces_fullscreen_tui_renderer(self):
        # #376 REVERSES the earlier classic pin: fullscreen keeps the WHOLE
        # conversation in its OWN app-internal scrollback (PgUp/PgDn, Ctrl+O),
        # confirmed by Anthropic's own docs to survive repeated compaction —
        # classic instead draws into tmux's NATIVE scrollback, which a
        # resize/relayout genuinely duplicates/loses bands of (upstream
        # anthropics/claude-code#84247 + #46834, both still open). Must also
        # override an existing "classic"/"default" value (a box that manually
        # switched back, or a pre-#376 install).
        out = airuleset.apply_managed_settings_defaults({})
        self.assertEqual(out["tui"], "fullscreen")
        self.assertEqual(out["tui"], airuleset.MANAGED_TUI)
        out = airuleset.apply_managed_settings_defaults({"tui": "default"})
        self.assertEqual(out["tui"], "fullscreen")
        out = airuleset.apply_managed_settings_defaults({"tui": "classic"})
        self.assertEqual(out["tui"], "fullscreen")

    def test_disables_the_input_box_prompt_suggestion(self):
        # #189: Claude Code renders its predicted-next-prompt suggestion as
        # dim SGR 246 text after the `❯` glyph. `tmux capture-pane -p` strips
        # attributes, so the watchdog's boundary classifiers cannot tell that
        # ghost from a genuinely typed draft — it read as a held draft on
        # every managed box. `promptSuggestionEnabled` is a REAL key in the
        # installed build (2.1.220 carries it in the same global-settings key
        # vector as effortLevel / autoCompactWindow / tui), never a guessed
        # name; it was present on dev1 ONLY as an unmanaged local edit and
        # absent on gatekeeper and montalu. Managed default so a push lands
        # and self-heals it everywhere. It removes the current SOURCE of the
        # ambiguity — it is NOT the delivery fix, which must work regardless
        # (the value is latched at process init, so sessions already running
        # keep rendering suggestions until they restart).
        out = airuleset.apply_managed_settings_defaults({})
        self.assertIs(out.get("promptSuggestionEnabled"), False)
        out = airuleset.apply_managed_settings_defaults(
            {"promptSuggestionEnabled": True})
        self.assertIs(out.get("promptSuggestionEnabled"), False)

    def test_preserves_other_keys(self):
        out = airuleset.apply_managed_settings_defaults(
            {"hooks": {"Stop": []}, "enabledPlugins": {"x": True}})
        self.assertEqual(out["hooks"], {"Stop": []})
        self.assertEqual(out["enabledPlugins"], {"x": True})
        self.assertEqual(out["effortLevel"], "xhigh")

    def test_idempotent_and_overrides_lower(self):
        once = airuleset.apply_managed_settings_defaults({"effortLevel": "medium"})
        twice = airuleset.apply_managed_settings_defaults(once)
        self.assertEqual(once, twice)
        self.assertEqual(twice["effortLevel"], "xhigh")  # raises a lower default

    def test_does_not_mutate_input(self):
        src = {"hooks": {}}
        airuleset.apply_managed_settings_defaults(src)
        self.assertNotIn("effortLevel", src)  # input untouched


class TestManagedModelDefault(TestCase):
    """apply_managed_settings_defaults also sets `model = MANAGED_MODEL`:
    since the 2026-08-13 user directive Opus 5 is BANNED everywhere and the
    managed MAIN default is Fable 5 (model-awareness.md ACTIVE policy);
    this is the SAME unconditional-managed-default treatment already
    applied to effortLevel/disableAgentView/tui, so every managed user on
    every box gets it on the next install — which is exactly what makes the
    ban self-healing (a stale Opus 5 leftover in settings.json is
    overwritten on the next push, the live dev1 regression the ticket's
    STEP 0 validation observed)."""

    def test_sets_managed_model(self):
        out = airuleset.apply_managed_settings_defaults({})
        self.assertEqual(out["model"], airuleset.MANAGED_MODEL)

    def test_managed_model_is_fable_5_with_1m_suffix(self):
        # the `[1m]` suffix is a DELIBERATE part of the managed id — it keeps
        # the 1M context window (verified against real `lastModelUsage`
        # entries in ~/.claude.json, which key the 1M variant as a distinct
        # `<model>[1m]` id) so this change never also shrinks context and
        # re-triggers context-loss regressions.
        self.assertEqual(airuleset.MANAGED_MODEL, "claude-fable-5[1m]")

    def test_overrides_an_existing_model_choice(self):
        # unconditional, like effortLevel/disableAgentView/tui — a managed
        # box always gets the managed default on the next install. The input
        # here is the BANNED Opus 5 id deliberately: a box a prior session
        # left parked on it must self-heal to the managed Fable default.
        out = airuleset.apply_managed_settings_defaults(
            {"model": "claude-opus-5[1m]"})
        self.assertEqual(out["model"], airuleset.MANAGED_MODEL)

    def test_idempotent(self):
        once = airuleset.apply_managed_settings_defaults({})
        twice = airuleset.apply_managed_settings_defaults(once)
        self.assertEqual(once["model"], twice["model"])


class TestManagedSubagentCapDefault(TestCase):
    """apply_managed_settings_defaults also sets the native settings.json
    `env` block's `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` (#288): the default
    200 is a CUMULATIVE per-session spawn cap, not a concurrency limit, and a
    long-running /goal-armed autopilot session (workers, reviewers,
    validators, ticket-validators, verifiers, TURBO parallel lanes) burns
    through it inside a day — hit live on gatekeeper 2026-08-07 ('Subagent
    spawn limit reached (200 of 200 agents spawned)'), losing the Agent tool
    mid-run. Same unconditional-managed-default treatment as
    effortLevel/disableAgentView/tui/model/promptSuggestionEnabled — applied
    fleet-wide (no full-authority-only carve-out: the cap is a per-session
    subagent-spawn ceiling, not a merge-authority concern, and reduced-
    authority sub-dev streams run equally long /goal loops)."""

    def test_sets_max_subagents_per_session_in_env_block(self):
        out = airuleset.apply_managed_settings_defaults({})
        self.assertEqual(
            out["env"]["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"],
            airuleset.MANAGED_MAX_SUBAGENTS_PER_SESSION)

    def test_value_is_a_thousand_as_a_string(self):
        # settings.json `env` values are environment-variable strings, like
        # every other documented key in that block (e.g. BASH_DEFAULT_TIMEOUT_MS) —
        # never a raw JSON number.
        out = airuleset.apply_managed_settings_defaults({})
        self.assertEqual(out["env"]["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"], "1000")
        self.assertIsInstance(out["env"]["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"], str)

    def test_preserves_other_env_keys(self):
        out = airuleset.apply_managed_settings_defaults(
            {"env": {"BASH_DEFAULT_TIMEOUT_MS": "120000"}})
        self.assertEqual(out["env"]["BASH_DEFAULT_TIMEOUT_MS"], "120000")
        self.assertEqual(
            out["env"]["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"],
            airuleset.MANAGED_MAX_SUBAGENTS_PER_SESSION)

    def test_overrides_a_lower_existing_value(self):
        out = airuleset.apply_managed_settings_defaults(
            {"env": {"CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION": "50"}})
        self.assertEqual(
            out["env"]["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"],
            airuleset.MANAGED_MAX_SUBAGENTS_PER_SESSION)

    def test_idempotent(self):
        once = airuleset.apply_managed_settings_defaults({})
        twice = airuleset.apply_managed_settings_defaults(once)
        self.assertEqual(once["env"], twice["env"])

    def test_does_not_mutate_the_input_env_dict(self):
        src_env = {"BASH_DEFAULT_TIMEOUT_MS": "120000"}
        src = {"env": src_env}
        airuleset.apply_managed_settings_defaults(src)
        self.assertNotIn("CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION", src_env)

    def test_a_non_dict_env_value_self_heals_instead_of_crashing(self):
        # An adversarial-review finding (#288): `dict(existing or {})` crashes
        # on a hand-corrupted/legacy settings.json whose `env` key is a
        # string/int/list rather than an object — `dict("oops")` raises
        # ValueError, escaping cmd_install's install step with no enclosing
        # try/except, and (worse) cmd_push's local-install call catches only
        # SystemExit, so this would land mid-way through a fleet deploy
        # (main already pushed, zero remote hosts updated). A non-dict `env`
        # is self-healed to a fresh dict rather than crashed on — same
        # discipline as every other malformed-input path in this repo (never
        # guess, never propagate an avoidable exception through a shared
        # deploy pipeline).
        for bad in ("oops", 5, ["a"], [["A", "B"]], True):
            out = airuleset.apply_managed_settings_defaults({"env": bad})
            self.assertEqual(
                out["env"]["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"],
                airuleset.MANAGED_MAX_SUBAGENTS_PER_SESSION)

    def test_none_env_still_works(self):
        # An explicit JSON null for "env" (distinct from the key being
        # absent) must keep working exactly like the absent-key case.
        out = airuleset.apply_managed_settings_defaults({"env": None})
        self.assertEqual(
            out["env"]["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"],
            airuleset.MANAGED_MAX_SUBAGENTS_PER_SESSION)


class TestManagedCleanupPeriodDefault(TestCase):
    """apply_managed_settings_defaults also sets `cleanupPeriodDays` (#376):
    the installed CC 2.1.227 binary's own Zod schema documents a NATIVE
    auto-cleanup of chat transcripts with a default of 30 days when the key
    is absent ("Number of days to retain chat transcripts before automatic
    cleanup (default: 30)") -- confirmed live, not guessed. `dev1` already
    carries a MANUAL (non-airuleset) override of 365 in its real
    settings.json; `david2` (a fresh sub-dev stream box, gatekeeper-
    provisioned 2026-08-08) has no override at all and is exposed to the
    30-day default. Same unconditional-managed-default treatment as
    effortLevel/disableAgentView/tui/model/promptSuggestionEnabled: never
    silently lose transcript history to a native default the user never
    configured. The value itself is CC's own suggested one, quoted verbatim
    in its cleanupPeriodDays-too-small validation-tip string ("set a large
    number (e.g. 3650 for ~10 years)") -- not an invented number."""

    def test_sets_cleanup_period_days(self):
        out = airuleset.apply_managed_settings_defaults({})
        self.assertEqual(out["cleanupPeriodDays"],
                          airuleset.MANAGED_CLEANUP_PERIOD_DAYS)

    def test_value_is_thirty_six_hundred_fifty_days(self):
        # CC's own error-message-suggested value for long retention -- see
        # the class docstring for the exact quoted source string.
        self.assertEqual(airuleset.MANAGED_CLEANUP_PERIOD_DAYS, 3650)

    def test_value_is_an_int_not_a_string(self):
        # Unlike the env-block CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION key
        # (env vars are always strings), cleanupPeriodDays is a top-level
        # settings.json key with a real Zod `int().positive()` type -- a
        # string value here would fail CC's own settings validation.
        out = airuleset.apply_managed_settings_defaults({})
        self.assertIsInstance(out["cleanupPeriodDays"], int)
        self.assertNotIsInstance(out["cleanupPeriodDays"], bool)

    def test_overrides_a_lower_existing_value(self):
        # Unconditional, like every other key in this function -- a managed
        # box always gets the managed default on the next install, even one
        # already carrying dev1's own manual 365 (or any other value).
        out = airuleset.apply_managed_settings_defaults({"cleanupPeriodDays": 30})
        self.assertEqual(out["cleanupPeriodDays"], 3650)
        out = airuleset.apply_managed_settings_defaults({"cleanupPeriodDays": 365})
        self.assertEqual(out["cleanupPeriodDays"], 3650)

    def test_idempotent(self):
        once = airuleset.apply_managed_settings_defaults({})
        twice = airuleset.apply_managed_settings_defaults(once)
        self.assertEqual(once["cleanupPeriodDays"], twice["cleanupPeriodDays"])

    def test_preserves_other_keys(self):
        out = airuleset.apply_managed_settings_defaults(
            {"hooks": {"Stop": []}, "cleanupPeriodDays": 7})
        self.assertEqual(out["hooks"], {"Stop": []})
        self.assertEqual(out["cleanupPeriodDays"], 3650)


class TestUltracodeLauncher(TestCase):
    """apply_ultracode_launcher manages the managed claude launcher (#77):
    a thin ~/.bashrc block of one-line functions that just exec a SCRIPT
    (script_path) carrying ALL the actual logic. This is the fix for the
    bug where a bashrc FUNCTION is parsed once at shell startup and then
    frozen in that shell's memory forever -- a `push` rewriting .bashrc had
    ZERO effect on an already-running panel shell, so ultracode (or any
    future flag change) silently kept resurrecting on every relaunch of a
    stale shell. Since the 2026-08-13 user directive ("by default vzdy
    ultracode") ultracode is the STANDING DEFAULT: every mode except the
    deliberate vanilla `plain` escape hatch carries the flag — reversing
    #53's session-only opt-in on the user's explicit dated instruction."""

    def _tmp(self, content=None):
        from pathlib import Path
        d = tempfile.mkdtemp()
        p = Path(d) / ".bashrc"
        s = Path(d) / ".claude" / "airuleset-claude-launch.sh"
        h = Path(d) / ".claude" / "airuleset-claude-history.py"
        pp = Path(d) / ".claude" / "airuleset-claude-history-popup.sh"
        if content is not None:
            p.write_text(content)
        return p, s, h, pp

    def test_appends_to_existing_bashrc_preserving_content(self):
        p, s, h, pp = self._tmp("export PATH=$PATH:/x\nalias ll='ls -la'\n")
        changed = airuleset.apply_ultracode_launcher(p, s, h, pp)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn("export PATH=$PATH:/x", text)           # preserved
        self.assertIn("alias ll='ls -la'", text)              # preserved
        self.assertIn("claude-ultracode()", text)
        self.assertIn(airuleset.ULTRACODE_MARK_START, text)
        self.assertIn(airuleset.ULTRACODE_MARK_END, text)

    def test_idempotent_bashrc_no_change_second_run(self):
        p, s, h, pp = self._tmp("# my rc\n")
        self.assertTrue(airuleset.apply_ultracode_launcher(p, s, h, pp))
        self.assertFalse(airuleset.apply_ultracode_launcher(p, s, h, pp))  # bashrc no-op

    def test_replaces_block_in_place_no_duplicate(self):
        p, s, h, pp = self._tmp("# rc\n")
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        # tamper inside the block, re-run -> block restored, exactly ONE block
        text = p.read_text().replace("claude-ultracode()", "BROKEN")
        p.write_text(text)
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        out = p.read_text()
        self.assertEqual(out.count(airuleset.ULTRACODE_MARK_START), 1)
        self.assertNotIn("BROKEN", out)
        self.assertIn("claude-ultracode()", out)

    def test_creates_bashrc_when_absent(self):
        from pathlib import Path
        d = tempfile.mkdtemp()
        p = Path(d) / ".bashrc"
        s = Path(d) / ".claude" / "airuleset-claude-launch.sh"
        h = Path(d) / ".claude" / "airuleset-claude-history.py"
        pp = Path(d) / ".claude" / "airuleset-claude-history-popup.sh"
        self.assertTrue(airuleset.apply_ultracode_launcher(p, s, h, pp))
        self.assertIn("claude()", p.read_text())

    def test_function_not_alias_and_has_plain_escape(self):
        p, s, h, pp = self._tmp()
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        text = p.read_text()
        self.assertIn("claude() {", text)
        self.assertNotIn("alias claude=", text)
        self.assertIn("claude-new()", text)
        self.assertIn("claude-ultracode()", text)
        self.assertIn("claude-plain()", text)
        self.assertIn("claude-fullscreen()", text)
        self.assertIn("claude-history()", text)

    def test_bashrc_block_has_no_flag_literals_only_script_calls(self):
        # THE ACCEPTANCE CRITERION (#77): the .bashrc block must contain NO
        # --settings / --model / --dangerously-skip-permissions literal --
        # only a call into the managed script. This is what makes a `push`
        # take effect in an already-running shell: nothing flag-shaped is
        # frozen in that shell's memory anymore.
        p, s, h, pp = self._tmp()
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        block = p.read_text().split(airuleset.ULTRACODE_MARK_START)[1]
        block = block.split(airuleset.ULTRACODE_MARK_END)[0]
        for literal in ("--settings", "--model", "--dangerously-skip-permissions",
                        "ultracode\":true", airuleset.MANAGED_MODEL,
                        "CLAUDE_CODE_NO_FLICKER",
                        "CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP"):
            self.assertNotIn(literal, block, block)
        self.assertIn(airuleset.CLAUDE_LAUNCH_SCRIPT_DEST.name, block)
        for fn, mode in (("claude", "default"), ("claude-new", "new"),
                         ("claude-ultracode", "ultracode"), ("claude-plain", "plain"),
                         ("claude-fullscreen", "fullscreen")):
            line = next(ln for ln in block.splitlines() if ln.startswith(f"{fn}() {{"))
            self.assertIn(f'"$HOME/.claude/{airuleset.CLAUDE_LAUNCH_SCRIPT_DEST.name}" {mode} "$@"',
                          line)
        history_line = next(ln for ln in block.splitlines()
                             if ln.startswith("claude-history() {"))
        self.assertIn(f'python3 "$HOME/.claude/{airuleset.CLAUDE_HISTORY_SCRIPT_DEST.name}" "$@"',
                       history_line)

    def test_writes_executable_script_at_script_path(self):
        p, s, h, pp = self._tmp()
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        self.assertTrue(s.exists())
        self.assertEqual(s.read_text(), airuleset.render_claude_launch_script())
        self.assertTrue(os.access(s, os.X_OK))

    def test_writes_executable_history_script_at_history_script_path(self):
        p, s, h, pp = self._tmp()
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        self.assertTrue(h.exists())
        self.assertEqual(h.read_text(), airuleset.render_claude_history_script())
        self.assertTrue(os.access(h, os.X_OK))

    def test_script_rewritten_unconditionally_every_call(self):
        # Unlike the bashrc block (idempotent no-op when unchanged), the
        # script is ALWAYS (re)written -- so tampering with it (or a stale
        # copy from a rollback) is self-healed on the very next install/push,
        # the same unconditional-rewrite guarantee the caveman shim gives.
        p, s, h, pp = self._tmp()
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        s.write_text("BROKEN")
        h.write_text("BROKEN")
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        self.assertEqual(s.read_text(), airuleset.render_claude_launch_script())
        self.assertEqual(h.read_text(), airuleset.render_claude_history_script())

    def test_ultracode_flag_present_in_every_mode_except_plain(self):
        # 2026-08-13 directive REVERSES #53: ultracode is the STANDING
        # DEFAULT, so the flag is baked into every launch mode except the
        # deliberate vanilla `plain` escape hatch (mirror of the --model
        # flag test below). new(x1) + ultracode(if/else x2)
        # + fullscreen(if/else x2) + default(if/else x2) = 7
        content = airuleset.render_claude_launch_script()
        self.assertEqual(content.count('"ultracode":true'), 7, content)
        plain_branch = content.split("plain)", 1)[1].split(";;", 1)[0]
        self.assertNotIn("ultracode", plain_branch)

    def test_no_flicker_env_var_only_in_fullscreen_branch(self):
        # #253: CLAUDE_CODE_NO_FLICKER=1 is the OPT-IN mitigation for the
        # proven upstream renderer defect (anthropics/claude-code#84247 /
        # #46834) that stacks duplicate/interleaved frames into tmux's
        # native scrollback. It must appear ONLY inside the script's own
        # `fullscreen)` branch -- never silently applied to default/new/
        # ultracode/plain, since it trades away native tmux copy-mode / OS
        # scrollback search and that tradeoff is the user's call, not a
        # forced default.
        content = airuleset.render_claude_launch_script()
        start = content.index("fullscreen)")
        end = content.index(";;", start)
        fullscreen_branch = content[start:end]
        self.assertIn("CLAUDE_CODE_NO_FLICKER=1", fullscreen_branch)
        rest = content[:start] + content[end + len(";;"):]
        self.assertNotIn("CLAUDE_CODE_NO_FLICKER", rest)

    def test_model_flag_present_in_every_mode_except_plain(self):
        # THE BUG (live on gatekeeper): a RESUMED session (-c) silently kept
        # its OLD model -- the launcher never passed --model, so `-c`
        # inherited whatever the prior transcript was started with. Fix:
        # bake `--model <MANAGED_MODEL>` into every mode except the
        # deliberate vanilla `plain` escape hatch.
        content = airuleset.render_claude_launch_script()
        expected = "--model '%s'" % airuleset.MANAGED_MODEL
        # new(x1) + ultracode(if/else x2) + default(if/else x2)
        # + fullscreen(if/else x2) = 7
        self.assertEqual(content.count(expected), 7, content)
        plain_branch = content.split("plain)", 1)[1].split(";;", 1)[0]
        self.assertNotIn("--model", plain_branch)

    def test_pressure_reap_disabled_in_every_managed_mode_except_plain(self):
        # #460 (user decision 2026-08-14): the managed launcher exports
        # CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1 into the CLI process env
        # so every managed session's long-wait run_in_background waiter
        # survives CC's memoryPressure reap (SIGTERM->SIGKILL in minutes on a
        # swap-thrashing box, #448). Fleet-wide, WITH a recorded rollback
        # criterion. It applies to every managed mode but NOT the vanilla
        # `plain` escape hatch -- the same "every mode except plain" placement
        # as the --model / ultracode flags above, so a deliberate stock-claude
        # reproduction via `claude-plain` stays uncontaminated (#445). It is an
        # env EXPORT (inherited across the `exec claude`), not a CLI flag, so
        # it appears ONCE as a guarded line before the mode `case`.
        content = airuleset.render_claude_launch_script()
        export = "export CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1"
        self.assertIn(export, content)
        # The export is a SINGLE guarded pre-`case` line (not a per-branch flag
        # like --model), so asserting it verbatim WITH its guard is what gives
        # the "except plain" invariant teeth -- a mutant that drops the guard
        # (so `plain` would ALSO get the export, contaminating the #445 vanilla
        # escape hatch) fails here. The bare `plain)`-case-body check below can
        # never see the export (it lives above the case), so it alone is
        # vacuous w.r.t. the guard (#460-review MAJOR).
        self.assertIn('[ "$mode" = plain ] || ' + export, content)
        plain_branch = content.split("plain)", 1)[1].split(";;", 1)[0]
        self.assertNotIn("PRESSURE_REAP", plain_branch)


class TestStreamNotifyOwnerRouting(TestCase):
    """notify.STREAM_NOTIFY_OWNER + resolve_owner() (airuleset#151/#259):
    before this, an automated persona's Discord routing (montalu/david) was
    ONLY ever hand-added per account, as a bashrc AIRULESET_NOTIFY_OWNER
    export -- and simap's own onboarding missed it, so its pings fell back
    to the shared main-channel thread instead of claude-zbynek.

    Checked directly inside notify.resolve_owner() (NOT via a bashrc export
    a live session's process environment could predate) so an already-running
    session routes correctly on its very next ping, no restart needed -- an
    adversarial review of an earlier bashrc-based version of this fix
    live-verified that a real already-running session on simap@subdev kept
    misrouting after the bashrc line was written, since the fix only reaches
    shells STARTED after the write."""

    def setUp(self):
        import notify
        self.notify = notify

    def test_stream_users_route_to_the_expected_owner(self):
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["simap"], "zbynek")
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["montalu"], "zbynek")
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["montalu2"], "zbynek")
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["montalu3"], "zbynek")
        # montalu4 is Marek's OWN dev stream (airuleset#295 — the user's own
        # statement, independently corroborated by odoo-erp#2961's
        # 2026-08-05 ACCESS DECISION comment: montalu4 is the ONLY
        # montalu-family account marek's SSH key was added to). Routing it
        # to zbynek was the #295 bug — this assertion was INVERTED from
        # "zbynek" as the RED half of that fix's regression test.
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["montalu4"], "marek")
        # montalu5/6/7/8 (airuleset#378): owner routing decision 2026-08-11 —
        # montalu5 is Marek's stream (like montalu4) -> claude-marek; the
        # other three are zbynek's -> claude-zbynek.
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["montalu5"], "marek")
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["montalu6"], "zbynek")
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["montalu7"], "zbynek")
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["montalu8"], "zbynek")
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["david"], "david")
        # miva1 (airuleset#300): phase-1 isolated stream, same shape as
        # simap -- its own tmux session name carries no Discord identity of
        # its own, so it redirects straight to zbynek's own thread.
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["miva1"], "zbynek")
        # david2/david3/david4 (airuleset#326): additional capacity for the
        # SAME external developer as david -- redirect to david's own
        # already-self-mapped thread (never a NEW DISCORD_MIRROR_DAVID2/3/4
        # local .env key, which this repo's code cannot provision/deploy at
        # all -- see #326's own design comment, mirroring the #300
        # precedent). This also inherits david's own real
        # DISCORD_MIRROR_DAVID=zbynek mirror for free once resolved.
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["david2"], "david")
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["david3"], "david")
        self.assertEqual(self.notify.STREAM_NOTIFY_OWNER["david4"], "david")
        # marek is deliberately absent: its own tmux session name already
        # resolves correctly via DISCORD_NOTIFICATION_CHANNEL_MAREK.
        self.assertNotIn("marek", self.notify.STREAM_NOTIFY_OWNER)

    def test_a_mapped_user_resolves_with_no_tmux_and_no_env_override(self):
        # THE ACTUAL FIX: no AIRULESET_NOTIFY_OWNER env var, no TMUX at all
        # (mirrors a fresh `bash -ic` probe, or any non-interactive hook
        # invocation) -- the mapped owner still resolves.
        with m.patch.dict(os.environ, {}, clear=True), \
                m.patch.object(self.notify, "_current_user", return_value="simap"):
            self.assertEqual(self.notify.resolve_owner(), "zbynek")

    def test_david_routes_to_its_own_owner_not_zbyneks(self):
        with m.patch.dict(os.environ, {}, clear=True), \
                m.patch.object(self.notify, "_current_user", return_value="david"):
            self.assertEqual(self.notify.resolve_owner(), "david")

    def test_miva1_resolves_to_zbynek_with_no_tmux_and_no_env_override(self):
        with m.patch.dict(os.environ, {}, clear=True), \
                m.patch.object(self.notify, "_current_user", return_value="miva1"):
            self.assertEqual(self.notify.resolve_owner(), "zbynek")

    def test_david_family_resolves_to_david_with_no_tmux_and_no_env_override(self):
        # airuleset#326: david2/david3/david4 redirect to david's own thread.
        for who in ("david2", "david3", "david4"):
            with m.patch.dict(os.environ, {}, clear=True), \
                    m.patch.object(self.notify, "_current_user", return_value=who):
                self.assertEqual(self.notify.resolve_owner(), "david", who)

    def test_montalu5_8_resolve_per_the_owner_routing_decision(self):
        # airuleset#378: montalu5 -> marek (Marek's stream, like montalu4),
        # montalu6/7/8 -> zbynek. Resolved directly inside resolve_owner()
        # with no TMUX and no env override, exactly like the other stream
        # personas above.
        for who, owner in (("montalu5", "marek"), ("montalu6", "zbynek"),
                           ("montalu7", "zbynek"), ("montalu8", "zbynek")):
            with m.patch.dict(os.environ, {}, clear=True), \
                    m.patch.object(self.notify, "_current_user", return_value=who):
                self.assertEqual(self.notify.resolve_owner(), owner, who)

    def test_env_override_still_wins_over_the_stream_map(self):
        # montalu/david keep a redundant hand-added bashrc export from
        # before this fix -- it must still take precedence (same value,
        # but the precedence itself must hold for a genuinely DIFFERENT
        # override too).
        with m.patch.dict(os.environ, {"AIRULESET_NOTIFY_OWNER": "someoneelse"}), \
                m.patch.object(self.notify, "_current_user", return_value="simap"):
            self.assertEqual(self.notify.resolve_owner(), "someoneelse")

    def test_an_unmapped_user_falls_through_to_tmux_resolution(self):
        # marek, newlevel, gatekeeper, montalu9 (a hypothetical future
        # account never added to the map -- montalu5 IS mapped now per
        # airuleset#378, so montalu9 is the genuinely-unmapped example):
        # no override at all -> "" when there's no TMUX either, exactly the
        # pre-existing behavior.
        for who in ("marek", "newlevel", "gatekeeper", "montalu9", ""):
            with m.patch.dict(os.environ, {}, clear=True), \
                    m.patch.object(self.notify, "_current_user", return_value=who):
                self.assertEqual(self.notify.resolve_owner(), "", who)

    def test_an_unmapped_user_with_inherited_tmux_resolves_to_that_panes_group(self):
        # #334: documents the ACTUAL misroute mechanism behind the
        # claude-david regression -- a headless caller (newlevel, unmapped)
        # whose ANCESTOR shell happens to sit inside an unrelated tmux pane
        # (group "david", live-reproduced on dev2 via `tmux list-sessions`)
        # inherits $TMUX unconditionally (tmux propagates it to every child
        # process, interactive or not) and resolves to THAT pane's group --
        # zero relationship to who the write's real owner is. This is
        # DELIBERATELY left unchanged (the rejected alternative in #334's
        # design comment: no reliable signal distinguishes "genuinely
        # interactive in this pane" from "inherited $TMUX from an ancestor",
        # and narrowing the fallback would break the many legitimate hook
        # callers that correctly rely on this exact inheritance today).
        # Callers whose notification is NOT "about this pane" must pass an
        # explicit --owner-name override instead (see cmd_notify's --body
        # wiring below).
        class _R:
            def __init__(self, out):
                self.stdout = out

        with m.patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,1234,0"},
                          clear=True), \
                m.patch.object(self.notify, "_current_user", return_value="newlevel"), \
                m.patch("notify.subprocess.run", return_value=_R("david-3")):
            self.assertEqual(self.notify.resolve_owner(), "david")

    # --- stream_redirect() (airuleset#212): the SAME map, for a caller
    # resolving SOMEONE/SOMETHING ELSE's raw owner string (watchdog.pane_owner()
    # has no _current_user()/env context of its own to resolve against). ---
    def test_stream_redirect_maps_known_stream_personas(self):
        self.assertEqual(self.notify.stream_redirect("montalu"), "zbynek")
        self.assertEqual(self.notify.stream_redirect("montalu2"), "zbynek")
        self.assertEqual(self.notify.stream_redirect("montalu3"), "zbynek")
        # montalu4 → marek (airuleset#295) — see the sibling assertion above.
        self.assertEqual(self.notify.stream_redirect("montalu4"), "marek")
        self.assertEqual(self.notify.stream_redirect("simap"), "zbynek")
        self.assertEqual(self.notify.stream_redirect("miva1"), "zbynek")
        # david is self-mapped — the redirect is a documented no-op for it.
        self.assertEqual(self.notify.stream_redirect("david"), "david")
        # david2/david3/david4 (airuleset#326) redirect to david's own thread.
        self.assertEqual(self.notify.stream_redirect("david2"), "david")
        self.assertEqual(self.notify.stream_redirect("david3"), "david")
        self.assertEqual(self.notify.stream_redirect("david4"), "david")

    def test_stream_redirect_passes_through_unmapped_and_empty(self):
        self.assertEqual(self.notify.stream_redirect("marek"), "marek")
        self.assertEqual(self.notify.stream_redirect("zbynek"), "zbynek")
        self.assertEqual(self.notify.stream_redirect("newlevel"), "newlevel")
        self.assertEqual(self.notify.stream_redirect(""), "")
        self.assertIsNone(self.notify.stream_redirect(None))


class TestStreamAuthorityHasNotifyRouting(TestCase):
    """Every stream in AUTHORITY_BY_USER must make an explicit Discord-
    routing decision -- either a notify.STREAM_NOTIFY_OWNER entry or a
    documented exemption -- or the NEXT new stream account repeats #259
    exactly as simap did (no onboarding step is guaranteed to catch it)."""

    EXEMPT = {"marek"}   # own tmux session name + own DISCORD_*_MAREK keys

    def test_every_authority_user_has_a_routing_decision(self):
        import notify
        missing = (set(airuleset.AUTHORITY_BY_USER)
                   - set(notify.STREAM_NOTIFY_OWNER) - self.EXEMPT)
        self.assertEqual(missing, set(), missing)


class TestReadinessCommentMatcher(TestCase):
    """`_is_readiness_comment` (#313 pt 2 adversarial review MAJOR-2) is a
    precise, LINE-ANCHORED matcher — the SAME contract
    `skills/process-subdev/templates/subdev-handoff-match.sh` (#1500)
    already enforces for the repo's own hand-off-label GitHub Actions
    workflow. A bare `"ready-for-review" in body.lower()` substring check
    re-introduces the EXACT over-match incident #1500 was written to fix:
    a comment merely MENTIONING the marker, or a GATEKEEPER finding
    comment quoting it, is not a genuine hand-off."""

    def test_a_genuine_bare_marker_matches(self):
        self.assertTrue(airuleset._is_readiness_comment(
            "READY-FOR-REVIEW: fork pushed, tests green"))

    def test_a_markdown_heading_form_matches(self):
        self.assertTrue(airuleset._is_readiness_comment(
            "## READY-FOR-REVIEW — everything green"))

    def test_the_cross_fork_review_phrase_matches(self):
        self.assertTrue(airuleset._is_readiness_comment(
            "Everything is green now.\nReady for gatekeeper cross-fork "
            "review."))

    def test_a_bare_mid_sentence_mention_does_not_match(self):
        self.assertFalse(airuleset._is_readiness_comment(
            "Note: earlier I said READY-FOR-REVIEW but that was premature, "
            "still fixing a bug."))

    def test_a_gatekeeper_finding_comment_never_matches(self):
        # Even though its own SECOND line quotes the marker verbatim.
        self.assertFalse(airuleset._is_readiness_comment(
            "**GATEKEEPER FINDING:** not ready.\n"
            "READY-FOR-REVIEW is NOT accurate here."))

    def test_a_heading_style_gatekeeper_opening_never_matches(self):
        # airuleset#340 adversarial-review MAJOR-2: this port's docstring
        # claims it enforces "the SAME contract" as
        # subdev-handoff-match.sh -- #340 widened that shell script's own
        # exclusion to also recognise a markdown-HEADING gatekeeper
        # opening ("## Gatekeeper review — BOUNCE", the real odoo-erp#2878
        # corpus shape), which this Python port had not been updated to
        # match. Live-reproduced against the pre-fix port: True (bug).
        self.assertFalse(airuleset._is_readiness_comment(
            "## Gatekeeper review — BOUNCE\n\n"
            "READY-FOR-REVIEW: was claimed but incomplete\n"))

    def test_a_deeper_heading_level_gatekeeper_opening_never_matches(self):
        self.assertFalse(airuleset._is_readiness_comment(
            "### Gatekeeper finding\n\n"
            "READY-FOR-REVIEW: was claimed but incomplete\n"))

    def test_a_heading_only_mentioning_gatekeeper_mid_title_still_matches(self):
        # Negative control mirroring subdev-handoff-match.sh's own -- a
        # heading merely mentioning "gatekeeper" as a topic word (not the
        # heading's own opening word, lower-case) is a genuine sub-dev
        # heading, not the gatekeeper speaking.
        self.assertTrue(airuleset._is_readiness_comment(
            "## Round 2 fixes for the gatekeeper bounce review\n\n"
            "READY-FOR-REVIEW: done\n"))

    def test_an_unrelated_comment_does_not_match(self):
        self.assertFalse(airuleset._is_readiness_comment("still working on it"))

    def test_non_string_body_does_not_match(self):
        self.assertFalse(airuleset._is_readiness_comment(None))
        self.assertFalse(airuleset._is_readiness_comment(123))
        self.assertFalse(airuleset._is_readiness_comment(""))


class TestCommentReadinessSignalGatekeeperExclusion(TestCase):
    """`_comment_readiness_signal` shares the SAME gatekeeper-opening
    exclusion regex as `_is_readiness_comment` -- airuleset#340
    adversarial-review MAJOR-2 found both consumers of
    `_READINESS_GATEKEEPER_FIRST_LINE_RE` diverged from
    subdev-handoff-match.sh's own widened contract identically."""

    def test_a_heading_style_gatekeeper_opening_signals_false_not_none(self):
        # False = an explicit gatekeeper-authored rejection (can override
        # an earlier True); None would be neutral and never override.
        self.assertIs(airuleset._comment_readiness_signal(
            "## Gatekeeper review — BOUNCE\n\n"
            "READY-FOR-REVIEW: was claimed but incomplete\n"), False)


class TestPaneOwnerAlwaysRedirected(TestCase):
    """Structural lock (airuleset#212 adversarial-review finding F2): every
    `pane_owner(...)` CALL SITE in watchdog/__init__.py (other than its own
    `def`) must be wrapped in `stream_redirect(...)` before the result can
    reach a notify `owner=` argument — a bare `pane_owner(pid, run)` bypasses
    notify.STREAM_NOTIFY_OWNER (#259) entirely, which is exactly how a stream
    persona's own account-wide alert (job 3) mis-routed to begin with. Two
    fix rounds found FOUR more un-redirected call sites (job 14's stash-skip
    ping, job 20's stall/drift/rearm give-up pings) beyond the two the first
    round covered — a source-level lock is cheaper than a full integration
    test per job and catches ANY future call site the same way."""

    def test_no_bare_pane_owner_call_reaches_the_source(self):
        import re
        src = Path(__file__).resolve().parent.parent.joinpath(
            "watchdog", "__init__.py").read_text(encoding="utf-8")
        # Every occurrence of the literal call `pane_owner(` that is NOT the
        # function's own `def pane_owner(` line.
        offending = []
        for mm in re.finditer(r"pane_owner\(", src):
            line_start = src.rfind("\n", 0, mm.start()) + 1
            line = src[line_start:src.find("\n", mm.start())]
            if line.lstrip().startswith("def pane_owner("):
                continue
            # The call itself must be immediately preceded by "stream_redirect("
            # (allowing only whitespace between) — i.e. `stream_redirect(pane_owner(`.
            before = src[:mm.start()]
            if not re.search(r"stream_redirect\(\s*$", before):
                lineno = src.count("\n", 0, mm.start()) + 1
                offending.append("line %d: %s" % (lineno, line.strip()))
        self.assertEqual(offending, [],
                         "pane_owner() call(s) not wrapped in stream_redirect(): "
                         + "; ".join(offending))


class TestAirulesetOwnerResolutionAlwaysRedirected(TestCase):
    """Structural lock (airuleset#302): the SAME discipline
    `TestPaneOwnerAlwaysRedirected` gives watchdog/__init__.py, extended to
    airuleset.py's OWN independent owner-resolution path.
    `_checkout_pane_owner`'s `owner_of(pid)` call (feeding
    `_notify_backfill_digest`'s send) was never wrapped in
    `notify.stream_redirect(...)` when #212 fixed every watchdog.py
    `pane_owner(...)` call site — a rare, manual CLI path is exactly the
    kind of call site that keeps getting missed (#212 itself needed FOUR
    rounds to close every watchdog.py gap). Every `owner_of(`/`pane_owner(`
    CALL SITE in airuleset.py (never a `def`/parameter-default line — those
    never have an immediate `(` after the bare name) must be immediately
    preceded by `stream_redirect(`."""

    def test_no_bare_owner_resolution_call_reaches_the_source(self):
        # #302 review MINOR-9: skipping ANY line starting with "def " (not
        # just the function's OWN `def owner_of(`/`def pane_owner(` line) is
        # materially broader than the sibling watchdog lock's exact-prefix
        # check -- it would silently hide a real future violation shaped
        # like `def go(): return owner_of(pid)`. Narrowed to match the
        # sibling's own exact-prefix shape.
        import re
        src = Path(__file__).resolve().parent.parent.joinpath(
            "airuleset.py").read_text(encoding="utf-8")
        offending = []
        for pat, def_prefix in ((r"\bowner_of\(", "def owner_of("),
                                (r"\bpane_owner\(", "def pane_owner(")):
            for mm in re.finditer(pat, src):
                line_start = src.rfind("\n", 0, mm.start()) + 1
                line = src[line_start:src.find("\n", mm.start())]
                if line.lstrip().startswith(def_prefix):
                    continue
                before = src[:mm.start()]
                if not re.search(r"stream_redirect\(\s*$", before):
                    lineno = src.count("\n", 0, mm.start()) + 1
                    offending.append("line %d: %s" % (lineno, line.strip()))
        self.assertEqual(offending, [],
                         "owner-resolution call(s) not wrapped in "
                         "stream_redirect(): " + "; ".join(offending))


class TestQuestionsThreadRouting(TestCase):
    """#296: a ❓ question ping routes to a SEPARATE per-owner thread
    (claude-<owner>-q) so it never mixes with ✅/card/api-error pings in the
    owner's normal thread — extends notification_channel()'s EXISTING
    per-owner cascade with a second, PARALLEL namespace (kind="questions"),
    never a new mechanism. kind="default" (the parameter's default) stays
    byte-for-byte the pre-#296 behaviour for every EXISTING caller (send(),
    the run-card, api-error) — none of them pass `kind=` at all."""

    def setUp(self):
        import notify
        self.notify = notify

    def test_questions_kind_prefers_the_q_channel(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "shared",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q": "zqthread"}
        self.assertEqual(
            self.notify.notification_channel(env=env, owner="zbynek",
                                              kind="questions"),
            "zqthread")
        # the DEFAULT kind must still resolve the NORMAL thread, unaffected —
        # ✅/card/api-error pings must never read the _Q value.
        self.assertEqual(
            self.notify.notification_channel(env=env, owner="zbynek"),
            "zthread")

    def test_questions_kind_falls_back_to_the_normal_thread_when_unconfigured(self):
        # An owner with no _Q thread provisioned yet keeps PRE-#296 behaviour:
        # questions land in their EXISTING thread, never silently drop to the
        # shared channel just because the new namespace isn't set up yet.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "shared",
               "DISCORD_NOTIFICATION_CHANNEL_MAREK": "mthread"}
        self.assertEqual(
            self.notify.notification_channel(env=env, owner="marek",
                                              kind="questions"),
            "mthread")

    def test_questions_kind_falls_back_to_shared_with_no_normal_thread_either(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "shared"}
        self.assertEqual(
            self.notify.notification_channel(env=env, owner="nobody",
                                              kind="questions"),
            "shared")

    def test_questions_kind_empty_owner_falls_back_to_shared(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "shared",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q": "zqthread"}
        self.assertEqual(
            self.notify.notification_channel(env=env, owner="", kind="questions"),
            "shared")


class _FakeDiscordThreads:
    """A minimal in-memory Discord thread-API double: GET on an anchor
    channel resolves a fixed parent/guild; POST creates a thread and
    remembers it; GET on the guild's active-threads listing returns
    everything created SO FAR. Lets a test simulate TWO separate boxes
    (two `provision_question_thread` calls with two different local .env
    files) sharing the SAME real Discord server state — the exact shape
    the #296 adversarial-review MAJOR finding (duplicate-thread creation)
    needs to reproduce."""

    def __init__(self, parent_id="parentchan", guild_id="g1"):
        self.parent_id = parent_id
        self.guild_id = guild_id
        self.created = []
        self._next = 1

    def __call__(self, token, method, path, payload=None):
        if method == "GET" and path.endswith("threads/archived/public"):
            return {"threads": []}
        if method == "GET" and path.startswith("channels/"):
            return {"parent_id": self.parent_id, "guild_id": self.guild_id}
        if method == "GET" and path == "guilds/%s/threads/active" % self.guild_id:
            return {"threads": list(self.created)}
        if method == "POST" and path == "channels/%s/threads" % self.parent_id:
            tid = "thread%d" % self._next
            self._next += 1
            self.created.append({"id": tid, "parent_id": self.parent_id,
                                 "name": payload["name"]})
            return {"id": tid}
        return None


class TestCreateAndProvisionQuestionsThread(TestCase):
    """#296: a real `claude-<owner>-q` Discord thread does not exist by
    default — no code in this repo has ever created a Discord thread before
    (the existing per-owner threads were configured by hand into the .env).
    `create_owner_question_thread` spawns it as a SIBLING of the owner's
    EXISTING thread (same Discord parent channel, found via one GET) — the
    same mechanism a human used to set up a per-owner thread, just automated.
    `provision_question_thread` is the idempotent find-then-create-and-persist
    action behind `notify --provision-question-thread`."""

    def setUp(self):
        import notify
        self.notify = notify
        # #330 round-2 adversarial review MAJOR: a test in THIS class that
        # calls provision_question_thread()/_env_upsert() without an
        # explicit env_path= used to fall through to _env_path()'s own
        # REAL default — this box's live
        # ~/.claude/channels/discord/.env — and genuinely corrupted it (a
        # fake fixture id silently overwrote the real
        # DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q, caught live via #330's
        # own end-to-end verification: a real ❓ send then failed with
        # HTTP 400). Patching _env_path() here makes an omitted env_path=
        # STRUCTURALLY harmless for every test in this class, present and
        # future — no longer "remember to pass env_path=" discipline.
        tmpdir = tempfile.mkdtemp(prefix="airuleset-envpath-isolation-")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        patcher = m.patch.object(notify, "_env_path",
                                 return_value=os.path.join(tmpdir, ".env"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_create_owner_question_thread_anchors_off_the_existing_thread(self):
        calls = []

        def fake_http(token, method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET":
                self.assertEqual(path, "channels/zthread")
                return {"parent_id": "parentchan"}
            if method == "POST":
                self.assertEqual(path, "channels/parentchan/threads")
                self.assertEqual(payload["name"], "claude-zbynek-q")
                self.assertEqual(payload["type"], 11)   # PUBLIC_THREAD
                return {"id": "newqthread"}
            return None

        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
        tid = self.notify.create_owner_question_thread(env, "zbynek",
                                                        http=fake_http)
        self.assertEqual(tid, "newqthread")
        self.assertEqual([c[0] for c in calls], ["GET", "POST"])

    def test_create_owner_question_thread_no_token_returns_empty(self):
        self.assertEqual(
            self.notify.create_owner_question_thread({}, "zbynek"), "")

    def test_create_owner_question_thread_no_owner_returns_empty(self):
        self.assertEqual(
            self.notify.create_owner_question_thread(
                {"DISCORD_BOT_TOKEN": "tok"}, ""), "")

    def test_create_owner_question_thread_no_existing_thread_returns_empty(self):
        # no per-owner or shared channel configured at all -> nothing to
        # anchor the new thread off of.
        env = {"DISCORD_BOT_TOKEN": "tok"}
        self.assertEqual(
            self.notify.create_owner_question_thread(
                env, "zbynek", http=lambda *a, **k: None),
            "")

    def test_create_owner_question_thread_failed_parent_lookup_returns_empty(self):
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
        self.assertEqual(
            self.notify.create_owner_question_thread(
                env, "zbynek", http=lambda *a, **k: None),
            "")

    def test_create_owner_question_thread_failed_post_returns_empty(self):
        def fake_http(token, method, path, payload=None):
            if method == "GET":
                return {"parent_id": "parentchan"}
            return None    # the thread-create POST failed
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
        self.assertEqual(
            self.notify.create_owner_question_thread(
                env, "zbynek", http=fake_http),
            "")

    def test_env_upsert_appends_new_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n")
            self.assertTrue(self.notify._env_upsert(
                p, "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q", "newid"))
            content = Path(p).read_text()
            self.assertIn("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=newid", content)
            self.assertIn("DISCORD_BOT_TOKEN=tok", content)   # untouched

    def test_env_upsert_replaces_existing_key_without_duplicating(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text(
                "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=old\nOTHER=1\n")
            self.notify._env_upsert(
                p, "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q", "new")
            lines = Path(p).read_text().splitlines()
            self.assertEqual(
                lines.count("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=new"), 1)
            self.assertNotIn("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=old", lines)
            self.assertIn("OTHER=1", lines)

    def test_env_upsert_creates_missing_file_and_dir(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "dir", ".env")
            self.assertTrue(self.notify._env_upsert(p, "K", "v"))
            self.assertIn("K=v", Path(p).read_text())

    def test_provision_question_thread_is_idempotent(self):
        # already configured -> return it, ZERO network calls at all.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q": "already"}
        calls = []
        result = self.notify.provision_question_thread(
            "zbynek", env=env,
            http=lambda *a, **k: calls.append(a) or None)
        self.assertEqual(result, "already")
        self.assertEqual(calls, [])

    def test_provision_question_thread_creates_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n"
                               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=zthread\n")
            env = {"DISCORD_BOT_TOKEN": "tok",
                   "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}

            def fake_http(token, method, path, payload=None):
                if method == "GET":
                    return {"parent_id": "parentchan"}
                return {"id": "brandnewq"}

            result = self.notify.provision_question_thread(
                "zbynek", env=env, env_path=p, http=fake_http)
            self.assertEqual(result, "brandnewq")
            self.assertIn("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=brandnewq",
                          Path(p).read_text())

    def test_provision_question_thread_no_owner_is_a_noop(self):
        self.assertEqual(self.notify.provision_question_thread(""), "")

    def test_provision_question_thread_failed_creation_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("X=1\n")
            result = self.notify.provision_question_thread(
                "zbynek", env={}, env_path=p, http=lambda *a, **k: None)
            self.assertEqual(result, "")
            self.assertEqual(Path(p).read_text(), "X=1\n")   # untouched

    # --- create=False (#330 F3: the automatic self-heal's own mode) -------
    def test_provision_question_thread_find_only_never_issues_a_create_post(self):
        calls = []

        def fake_http(token, method, path, payload=None):
            calls.append((method, path))
            if method == "GET" and path == "channels/zthread":
                return {"parent_id": "parentchan", "guild_id": "g1"}
            return {"threads": []}   # genuinely nothing to find anywhere

        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
        result = self.notify.provision_question_thread(
            "zbynek", env=env, create=False, http=fake_http)
        self.assertEqual(result, "")
        self.assertFalse(any(meth == "POST" for meth, _ in calls),
                         "create=False must NEVER issue a create POST")

    def test_provision_question_thread_find_only_still_finds_an_existing_thread(self):
        # env_path= is MANDATORY here (and in every sibling test that can
        # reach _env_upsert's WRITE branch) — omitting it defaults to the
        # REAL ~/.claude/channels/discord/.env (_env_path()'s own default)
        # and this call's `create=False` still reaches an _env_upsert once
        # `find` succeeds. A prior version of this test omitted it and
        # genuinely OVERWROTE this box's live DISCORD_NOTIFICATION_CHANNEL_
        # ZBYNEK_Q with the fake "found1" fixture value — caught live via
        # #330's own end-to-end verification (a real ❓ send then failed
        # with HTTP 400, posting to the bogus "found1" "channel"). Repaired
        # via a real, read-only find_owner_question_thread rediscovery.
        def fake_http(token, method, path, payload=None):
            if method == "GET" and path == "channels/zthread":
                return {"parent_id": "parentchan", "guild_id": "g1"}
            if method == "GET" and path == "guilds/g1/threads/active":
                return {"threads": [{"id": "found1", "parent_id": "parentchan",
                                     "name": "claude-zbynek-q"}]}
            return None

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n"
                               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=zthread\n")
            env = {"DISCORD_BOT_TOKEN": "tok",
                   "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
            result = self.notify.provision_question_thread(
                "zbynek", env=env, env_path=p, create=False, http=fake_http)
            self.assertEqual(result, "found1")
            self.assertIn("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=found1",
                          Path(p).read_text())

    def test_provision_question_thread_default_still_creates(self):
        # The explicit, human-typed CLI path (create=True, the default)
        # must be COMPLETELY unaffected by adding the create= parameter.
        # env_path= isolation — see the comment on the sibling test above.
        def fake_http(token, method, path, payload=None):
            if method == "GET":
                return {"parent_id": "parentchan"}
            return {"id": "createdid"}

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n"
                               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=zthread\n")
            env = {"DISCORD_BOT_TOKEN": "tok",
                   "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
            result = self.notify.provision_question_thread(
                "zbynek", env=env, env_path=p, http=fake_http)
            self.assertEqual(result, "createdid")
            self.assertIn("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=createdid",
                          Path(p).read_text())

    # --- find_owner_question_thread (adversarial-review MAJOR fix) --------
    def test_find_owner_question_thread_matches_active_thread(self):
        def fake_http(token, method, path, payload=None):
            if method == "GET" and path == "channels/zthread":
                return {"parent_id": "parentchan", "guild_id": "g1"}
            if method == "GET" and path == "guilds/g1/threads/active":
                return {"threads": [
                    {"id": "wrong", "parent_id": "parentchan",
                     "name": "claude-zbynek"},
                    {"id": "found123", "parent_id": "parentchan",
                     "name": "claude-zbynek-q"}]}
            return None
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
        self.assertEqual(
            self.notify.find_owner_question_thread(env, "zbynek",
                                                    http=fake_http),
            "found123")

    def test_find_owner_question_thread_falls_back_to_archived(self):
        def fake_http(token, method, path, payload=None):
            if method == "GET" and path == "channels/zthread":
                return {"parent_id": "parentchan", "guild_id": "g1"}
            if method == "GET" and path == "guilds/g1/threads/active":
                return {"threads": []}
            if (method == "GET"
                    and path == "channels/parentchan/threads/archived/public"):
                return {"threads": [{"id": "archivedq",
                                     "name": "claude-zbynek-q"}]}
            return None
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
        self.assertEqual(
            self.notify.find_owner_question_thread(env, "zbynek",
                                                    http=fake_http),
            "archivedq")

    def test_find_owner_question_thread_returns_empty_when_genuinely_absent(self):
        def fake_http(token, method, path, payload=None):
            if method == "GET" and path == "channels/zthread":
                return {"parent_id": "parentchan", "guild_id": "g1"}
            if method == "GET":
                return {"threads": []}
            return None
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
        self.assertEqual(
            self.notify.find_owner_question_thread(env, "zbynek",
                                                    http=fake_http), "")

    def test_find_owner_question_thread_no_token_or_owner_is_empty(self):
        self.assertEqual(self.notify.find_owner_question_thread({}, "zbynek"), "")
        self.assertEqual(self.notify.find_owner_question_thread(
            {"DISCORD_BOT_TOKEN": "tok"}, ""), "")

    def test_provision_question_thread_reuses_an_existing_thread_from_another_box(self):
        # #296 adversarial-review MAJOR: the questions-thread id is BOX-LOCAL
        # non-git config, so provisioning the SAME owner from a SECOND box
        # (a separate local .env) must FIND the thread the first box already
        # created on the real Discord server, never fork a duplicate.
        backend = _FakeDiscordThreads()
        with tempfile.TemporaryDirectory() as da, \
                tempfile.TemporaryDirectory() as db:
            pa = os.path.join(da, ".env")
            pb = os.path.join(db, ".env")
            Path(pa).write_text("DISCORD_BOT_TOKEN=tok\n")
            Path(pb).write_text("DISCORD_BOT_TOKEN=tok\n")
            env = {"DISCORD_BOT_TOKEN": "tok",
                   "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}

            first = self.notify.provision_question_thread(
                "zbynek", env=dict(env), env_path=pa, http=backend)
            self.assertTrue(first)
            self.assertEqual(len(backend.created), 1)

            second = self.notify.provision_question_thread(
                "zbynek", env=dict(env), env_path=pb, http=backend)
            self.assertEqual(
                second, first,
                "box B must FIND box A's thread, never create a second one")
            self.assertEqual(len(backend.created), 1,
                             "no duplicate thread was created")
            self.assertIn(
                "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=%s" % first,
                Path(pb).read_text())

    # --- anchor never falls back to the shared channel (THEORETICAL fix) --
    def test_create_owner_question_thread_never_anchors_off_the_shared_channel(self):
        calls = []

        def fake_http(token, method, path, payload=None):
            calls.append(path)
            return None
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ID": "sharedthread"}
        self.assertEqual(
            self.notify.create_owner_question_thread(env, "zbynek",
                                                      http=fake_http),
            "")
        self.assertEqual(calls, [],
                         "must refuse before ever querying the shared channel")

    def test_find_owner_question_thread_never_anchors_off_the_shared_channel(self):
        calls = []

        def fake_http(token, method, path, payload=None):
            calls.append(path)
            return None
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ID": "sharedthread"}
        self.assertEqual(
            self.notify.find_owner_question_thread(env, "zbynek",
                                                    http=fake_http),
            "")
        self.assertEqual(calls, [])

    # --- _env_upsert hardening (MINOR fixes) -------------------------------
    def test_env_upsert_tolerates_non_utf8_bytes_without_raising(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            with open(p, "wb") as fh:
                fh.write(b"DISCORD_BOT_TOKEN=tok\n\xffBROKEN=1\n")
            self.assertTrue(self.notify._env_upsert(p, "NEW_KEY", "v"))
            out = Path(p).read_text(errors="replace")
            self.assertIn("NEW_KEY=v", out)
            self.assertIn("DISCORD_BOT_TOKEN=tok", out)

    def test_env_upsert_writes_atomically_no_tmp_file_survives(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("X=1\n")
            self.notify._env_upsert(p, "Y", "2")
            # A DIRECTORY listing, not a fixed `p + ".tmp"` literal — #330's
            # concurrency fix (below) gives every call its OWN unique tmp
            # name (tempfile.mkstemp), so a leftover under any OTHER name
            # would slip past a check anchored on the old fixed literal.
            leftovers = [f for f in os.listdir(d) if f != os.path.basename(p)]
            self.assertEqual(
                leftovers, [],
                "the atomic tmp file must not survive a successful write")

    def test_env_upsert_writes_via_os_replace(self):
        # A discriminating mutant check for atomicity (the "no .tmp
        # survives" assertion above is TRUE for a plain non-atomic write
        # too, so it alone proves nothing) — the write must go through
        # os.replace(tmp, path), mirroring _save_questions()'s own
        # established atomic-write shape in this same module.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("X=1\n")
            with m.patch("notify.os.replace") as fake_replace:
                self.notify._env_upsert(p, "Y", "2")
            fake_replace.assert_called_once()
            src, dst = fake_replace.call_args[0]
            self.assertTrue(str(src).endswith(".tmp"), src)
            self.assertEqual(dst, p)

    def test_env_upsert_unlinks_the_tmp_file_on_a_failed_replace(self):
        # #330 round-2 adversarial review MINOR 3: the per-call unique
        # tempfile.mkstemp fix (F1) means a FAILED write now leaves an
        # ORPHANED, uniquely-named tmp file behind forever — the old
        # fixed-name implementation leaked at most ONE stray file (silently
        # overwritten by the next successful call); the new one leaks a
        # NEW file on every failure. Measured: 3 simulated failures ->
        # 3 distinct leftover .tmp files. os.replace failing here (e.g. a
        # cross-device rename, a permissions wobble) must not litter.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("X=1\n")
            with m.patch("notify.os.replace", side_effect=OSError("boom")):
                result = self.notify._env_upsert(p, "Y", "2")
            self.assertFalse(result)
            leftovers = [f for f in os.listdir(d) if f != os.path.basename(p)]
            self.assertEqual(
                leftovers, [],
                "a failed os.replace must not leave an orphaned tmp file")

    def test_env_upsert_concurrent_writers_never_publish_a_corrupted_file(self):
        # #330 adversarial-review F1 (CRITICAL): the OLD implementation
        # shared ONE FIXED tmp path (`path + ".tmp"`) across every caller —
        # two overlapping writers could interleave so a LATER writer's
        # os.replace published an EARLIER writer's still-being-written
        # (truncated/empty) tmp, destroying every key in the file including
        # DISCORD_BOT_TOKEN. Measured live: ~3.2% empty-file rate at 2
        # concurrent writers x 3000 upserts each. This reproduces the SAME
        # shape against the REAL function with genuine OS threads and real
        # file I/O — no mocking, because the defect is specifically about
        # real concurrent filesystem operations racing. The rep count
        # (2000/thread) is EMPIRICALLY calibrated on this box: 300/thread
        # never reproduced it in several tries (this filesystem's race
        # window is narrower than the reviewer's own environment), while
        # 2000/thread reproduced a corrupted (token-destroyed) file on the
        # unfixed code every time it was tried.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n")

            def hammer(owner):
                for i in range(2000):
                    self.notify._env_upsert(
                        p, "DISCORD_NOTIFICATION_CHANNEL_%s_Q" % owner,
                        "id%d" % i)

            threads = [threading.Thread(target=hammer, args=("ZBYNEK",)),
                      threading.Thread(target=hammer, args=("MAREK",))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            content = Path(p).read_text()
            self.assertGreater(
                len(content), 0,
                "the .env must never end up EMPTY from concurrent writers")
            self.assertIn(
                "DISCORD_BOT_TOKEN=tok", content,
                "the bot token must survive concurrent, unrelated writers")


class TestTmuxHistoryLimit(TestCase):
    """apply_tmux_history_limit ensures ~/.tmux.conf carries the managed
    tmux block: history-limit 50000 (#235: tmux's built-in default of 2000
    lines plus the current CC renderer's frame-stacking on re-render events
    made real scrollback holey within minutes under agentic load -- measured
    live: active panes saturated at ~1940/2000), PLUS default-size 176x50
    (#236: the fixed size new windows get). #236 originally also shipped
    `window-size manual`, but #241 found it CRASHES tmux 3.4's server at
    startup outright (`server exited unexpectedly`) -- confirmed live
    against the real 3.4 binary every managed box runs, the only version
    Ubuntu 24.04 noble ships -- so it was removed at the source; see
    TestTmuxWindowSizeRemoved below for the dedicated lock. Same idempotent-
    marker-block shape as apply_ultracode_launcher (#77) -- create if
    missing, rewrite CONTENT in place if present, never touch anything
    outside the markers -- plus a live-apply on any running tmux server via
    an injectable `run` (never a real tmux call, never a keystroke, in
    these tests). #236's own incident history settled that `resize-window`
    is NEVER invoked, in any code path -- see TestTmuxWindowSizeNoResize
    below for the structural lock."""

    def _tmp(self, content=None):
        d = tempfile.mkdtemp()
        p = Path(d) / ".tmux.conf"
        if content is not None:
            p.write_text(content)
        return p

    def test_creates_file_when_absent_with_the_managed_block(self):
        d = tempfile.mkdtemp()
        p = Path(d) / ".tmux.conf"
        changed = airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        self.assertTrue(changed)
        self.assertTrue(p.exists())
        text = p.read_text()
        self.assertIn(airuleset.TMUX_MARK_START, text)
        self.assertIn(airuleset.TMUX_MARK_END, text)
        # #241: the block renders only the two SURVIVING managed options --
        # window-size manual crashes tmux 3.4 at server start and was
        # removed at the source (see TestTmuxWindowSizeRemoved).
        self.assertIn("set-option -g history-limit 50000", text)
        self.assertIn("set-option -g default-size 176x50", text)
        # #254: detached grouped-session duplicates (zbynek-1..4 piling up
        # per attach) self-clean via tmux's own destroy-unattached, down to
        # exactly one survivor per group -- see TestTmuxDestroyUnattached
        # below for the dedicated keep-last-not-keep-group lock.
        self.assertIn("set-option -g destroy-unattached keep-last", text)
        self.assertNotIn("window-size", text)
        # never the resize-window/list-windows shape this ticket's incident
        # history explicitly rejected -- see TestTmuxWindowSizeNoResize.
        self.assertNotIn("resize-window", text)
        # #267: the Shift+PgUp/PgDn keyboard-scrollback bindings. #338:
        # S-PageUp is now conditional (native Ctrl+O inside a claude pane,
        # else the byte-identical copy-mode -eu fallback) -- see
        # TestTmuxScrollbackKeybinds for the dedicated lock + live proof.
        self.assertIn(
            'bind-key -n S-PageUp if -F '
            '"#{==:#{pane_current_command},claude}" '
            '"send-keys C-o" "copy-mode -eu"', text)
        self.assertIn(
            "bind-key -T copy-mode S-PageDown send-keys -X page-down", text)
        self.assertIn(
            "bind-key -T copy-mode-vi S-PageDown send-keys -X page-down", text)
        # #289: the one-keystroke claude-history popup, prefix-h only as
        # of #376 (S-F1/S-DC removed -- see the module comment on
        # TMUX_POPUP_PREFIX_KEY), invoking the popup script by its own
        # absolute path.
        popup_script = str(airuleset.CLAUDE_HISTORY_POPUP_SCRIPT_DEST)
        self.assertNotIn("S-F1", text)
        self.assertNotIn("S-DC", text)
        self.assertIn("bind-key h display-popup", text)
        self.assertIn(popup_script, text)
        self.assertIn('-d "#{pane_current_path}"', text)
        self.assertIn("-T claude-history", text)

    def test_appends_to_existing_conf_preserving_content_byte_for_byte(self):
        original = "set -g mouse on\nset -g status-bg colour234\n"
        p = self._tmp(original)
        changed = airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn(original, text)  # untouched, byte-for-byte
        self.assertIn(airuleset.TMUX_MARK_START, text)
        self.assertIn("set-option -g history-limit 50000", text)

    def test_rewrites_stale_block_content_in_place_preserving_surroundings(self):
        stale_block = (
            f"{airuleset.TMUX_MARK_START}\n"
            "set-option -g history-limit 2000\n"
            f"{airuleset.TMUX_MARK_END}"
        )
        original = f"set -g mouse on\n\n{stale_block}\n\nset -g status-bg colour234\n"
        p = self._tmp(original)
        changed = airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn("set -g mouse on", text)
        self.assertIn("set -g status-bg colour234", text)
        self.assertEqual(text.count(airuleset.TMUX_MARK_START), 1)
        self.assertNotIn("history-limit 2000", text)
        self.assertIn("history-limit 50000", text)

    def test_downgrades_236_three_line_block_dropping_window_size_preserving_surroundings(self):
        # A conf still carrying #236's ORIGINAL three-line block (history-
        # limit, the crashing window-size manual, default-size -- what a
        # box's ~/.tmux.conf looked like before #241's hand-removal, or
        # what an unpatched box still has) must self-heal on the very next
        # run: the block CONTENT is rewritten in place to the new two-line
        # form with window-size dropped -- the surroundings stay byte-
        # identical, exactly like the stale-value rewrite case above.
        pre_241_block = (
            f"{airuleset.TMUX_MARK_START}\n"
            "set-option -g history-limit 50000\n"
            "set-option -g window-size manual\n"
            "set-option -g default-size 176x50\n"
            f"{airuleset.TMUX_MARK_END}"
        )
        original = f"set -g mouse on\n\n{pre_241_block}\n\nset -g status-bg colour234\n"
        p = self._tmp(original)
        changed = airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn("set -g mouse on", text)
        self.assertIn("set -g status-bg colour234", text)
        self.assertEqual(text.count(airuleset.TMUX_MARK_START), 1)
        self.assertIn("set-option -g history-limit 50000", text)
        self.assertIn("set-option -g default-size 176x50", text)
        self.assertNotIn("window-size", text)
        # the surrounding non-managed lines are unchanged, and only ONE
        # blank-line-separated block sits between them (no duplication).
        # Written out LITERALLY (not built from render_tmux_history_block()
        # itself) so this also locks the exact line ORDER and content, not
        # just placement -- a self-referential expected string would pass
        # even if the two set-option lines were emitted out of order.
        popup_script = str(airuleset.CLAUDE_HISTORY_POPUP_SCRIPT_DEST)
        expected = (
            "set -g mouse on\n\n"
            f"{airuleset.TMUX_MARK_START}\n"
            "set-option -g history-limit 50000\n"
            "set-option -g destroy-unattached keep-last\n"
            "set-option -g default-size 176x50\n"
            'bind-key -n S-PageUp if -F '
            '"#{==:#{pane_current_command},claude}" '
            '"send-keys C-o" "copy-mode -eu"\n'
            "bind-key -T copy-mode S-PageDown send-keys -X page-down\n"
            "bind-key -T copy-mode-vi S-PageDown send-keys -X page-down\n"
            # #376: S-F1/S-DC removed -- prefix+h is the ONE surviving
            # popup binding, no `-e AIRULESET_POPUP_MODE=` flag exists
            # any more (the popup script is unconditional).
            f'bind-key h display-popup -E -w 96% -h 96% -d "#{{pane_current_path}}" '
            f'-T claude-history {popup_script}\n'
            f"{airuleset.TMUX_MARK_END}"
            "\n\nset -g status-bg colour234\n"
        )
        self.assertEqual(text, expected)

    def test_idempotent_second_run_is_a_no_op(self):
        p = self._tmp("# my tmux conf\n")
        self.assertTrue(airuleset.apply_tmux_history_limit(p, run=lambda argv: None))
        before_text = p.read_text()
        before_mtime = p.stat().st_mtime_ns
        changed = airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        self.assertFalse(changed)
        self.assertEqual(p.read_text(), before_text)
        self.assertEqual(p.stat().st_mtime_ns, before_mtime)

    def test_live_applies_via_injected_run_regardless_of_server_state(self):
        # Keystroke-free, safe: the history-limit set-option is live-
        # applied against a running server -- exactly #235's original,
        # already-shipped, already-proven-safe scope. default-size is
        # DELIBERATELY never live-applied via a real tmux subprocess call
        # (see TestTmuxWindowSizeRemoved below): a post-implementation
        # adversarial review, independently reproduced on this box's own
        # live tmux 3.7b binary via a real attached pty client, PROVED that
        # flipping `window-size` to `manual` against a RUNNING server
        # immediately snaps every window back to its stored/created size
        # regardless of the attached client's current size -- a live,
        # disruptive resize event with NO resize-window call and NO
        # #{...} format-expansion query involved at all. #241 found that
        # the SAME `window-size manual` option ALSO crashes tmux 3.4's
        # server outright at startup -- confirmed live against the real
        # 3.4 binary every managed box runs -- so it was removed from the
        # managed block entirely, at the source, rather than merely kept
        # conf-only. The managed ~/.tmux.conf block still carries the two
        # surviving options; they take effect for the NEXT server/session/
        # window, the same safe path already established for resize-
        # window itself.
        #
        # #267: the three Shift+PgUp/PgDn `bind-key` calls ARE ALSO live-
        # applied (a pure key-table registration -- none of window-size's
        # live-apply hazard, see the module comment above
        # render_tmux_history_block). #254: destroy-unattached is ALSO
        # live-applied, right after history-limit -- unlike window-size it
        # only ever evaluates sessions with ZERO attached clients, so it
        # structurally cannot disturb anything on screen, and live-applying
        # it immediately self-heals any ALREADY-existing detached grouped
        # pile-up on the very next push, with no new attach/detach cycle
        # needed -- verified live against a real tmux 3.7b server (see the
        # design comment on #254). #289: the claude-history popup
        # `bind-key` call (prefix-h -- S-F1/S-DC removed by #376, see the
        # module comment on TMUX_POPUP_PREFIX_KEY) is ALSO live-applied,
        # same safety class. #376 CLEANUP: two trailing `unbind-key`
        # calls remove S-F1/S-DC from an ALREADY-RUNNING server that was
        # live-bound before this fix deployed -- rewriting the conf file
        # alone does not retroactively unbind a live key-table entry.
        # Total: history-limit + destroy-unattached + 3 scrollback binds
        # + 1 popup bind + 2 unbinds = 8 calls.
        p = self._tmp()
        calls = []
        airuleset.apply_tmux_history_limit(p, run=calls.append)
        self.assertEqual(len(calls), 8)
        self.assertEqual(calls[0], ["tmux", "set-option", "-g", "history-limit", "50000"])
        self.assertEqual(calls[1], ["tmux", "set-option", "-g", "destroy-unattached", "keep-last"])
        self.assertEqual(calls[2], [
            "tmux", "bind-key", "-n", "S-PageUp", "if", "-F",
            "#{==:#{pane_current_command},claude}",
            "send-keys C-o", "copy-mode -eu"])
        self.assertEqual(calls[3], ["tmux", "bind-key", "-T", "copy-mode", "S-PageDown",
                                     "send-keys", "-X", "page-down"])
        self.assertEqual(calls[4], ["tmux", "bind-key", "-T", "copy-mode-vi", "S-PageDown",
                                     "send-keys", "-X", "page-down"])
        self.assertEqual(calls[5], ["tmux"] + airuleset.TMUX_POPUP_BIND_ARGVS[0])
        self.assertEqual(calls[6], ["tmux", "unbind-key", "-n", "S-F1"])
        self.assertEqual(calls[7], ["tmux", "unbind-key", "-n", "S-DC"])

    def test_a_failing_keybind_call_does_not_skip_the_remaining_ones(self):
        # #267: each live-apply call is independently guarded -- a runner
        # that raises on the SECOND call must not prevent the third/fourth
        # from being attempted.
        p = self._tmp()
        calls = []

        def _runner(argv):
            calls.append(argv)
            if len(calls) == 2:
                raise OSError("transient failure on this one call")
            return None

        airuleset.apply_tmux_history_limit(p, run=_runner)
        self.assertEqual(len(calls), 8)

    def test_a_nonzero_rc_keybind_call_does_not_skip_the_remaining_ones(self):
        # ADVERSARIAL-REVIEW FINDING (#267, MAJOR -- F1): the RAISING case
        # above only covers half of #235's own already-documented asymmetry
        # (test_live_apply_nonzero_return_without_raising_is_logged):
        # subprocess.run does NOT raise on a nonzero exit code with no
        # check=True -- a real `tmux bind-key` against a dead socket exits
        # nonzero WITHOUT raising. A mutant that `break`s out of the
        # live-apply loop inside the `if rc:` branch survived the suite
        # before this test existed (43/43 passed) -- prove the SAME
        # per-call independence holds for a non-raising nonzero-rc result,
        # not just for a raised exception.
        p = self._tmp()
        calls = []

        class _FakeFailedResult:
            returncode = 1
            stderr = "no server running on default socket"

        def _runner(argv):
            calls.append(argv)
            if len(calls) == 2:
                return _FakeFailedResult()
            return None

        airuleset.apply_tmux_history_limit(p, run=_runner)
        self.assertEqual(len(calls), 8)

    def test_live_apply_failure_is_silently_ignored(self):
        # "ignore failure when no server" -- a raising run() must not
        # propagate, and must not affect the conf-file write result.
        def _boom(argv):
            raise OSError("no server running on default socket")
        p = self._tmp()
        changed = airuleset.apply_tmux_history_limit(p, run=_boom)
        self.assertTrue(changed)  # the file write still succeeded

    def test_live_apply_nonzero_return_without_raising_is_logged(self):
        # ADVERSARIAL-REVIEW FINDING (#235, MAJOR): `subprocess.run` does
        # NOT raise on a nonzero exit code with no `check=True` -- a real
        # `tmux set-option` against a dead/nonexistent socket exits 1
        # WITHOUT raising, so a bare try/except around the call alone
        # silently swallows it with ZERO log output (contradicting the
        # docstring's "logged for visibility" claim: it was really only
        # "logged if the call happened to raise"). A CompletedProcess-
        # shaped fake result (never an exception) must still be surfaced.
        import io
        from contextlib import redirect_stderr

        class _FakeFailedResult:
            returncode = 1
            stderr = "no server running on default socket"

        p = self._tmp()
        buf = io.StringIO()
        with redirect_stderr(buf):
            changed = airuleset.apply_tmux_history_limit(
                p, run=lambda argv: _FakeFailedResult())
        self.assertTrue(changed)  # conf write still succeeds regardless
        self.assertIn("no server running", buf.getvalue())

    def test_malformed_reversed_markers_self_heal_without_data_loss(self):
        # ADVERSARIAL-REVIEW FINDING (#235, MAJOR): an externally-corrupted
        # conf with END appearing BEFORE START must never be treated as a
        # valid pair to replace in place -- a naive whole-file `START.*?END`
        # regex would (on a SECOND run, once a fresh clean block has been
        # appended after the stray markers) span from the stray START all
        # the way to the fresh block's END, silently deleting every real
        # tmux directive sitting in between. Correct behavior: never touch
        # or merge with an unpaired marker; append a fresh clean block
        # instead, and stay a stable no-op afterwards -- zero data loss,
        # across any number of repeated runs.
        malformed = (
            "set -g mouse on\n\n"
            f"{airuleset.TMUX_MARK_END}\n"
            "set-option -g history-limit 2000\n"
            f"{airuleset.TMUX_MARK_START}\n\n"
            "set -g status-bg colour234\n"
        )
        p = self._tmp(malformed)
        changed1 = airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        self.assertTrue(changed1)
        after_first = p.read_text()
        self.assertIn("set -g mouse on", after_first)              # untouched
        self.assertIn("set -g status-bg colour234", after_first)   # untouched
        self.assertIn("history-limit 50000", after_first)          # fresh block landed

        # SECOND run: must be a clean no-op, and crucially must NOT eat the
        # content sitting between the stray markers and the fresh block.
        changed2 = airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        self.assertFalse(changed2)
        after_second = p.read_text()
        self.assertEqual(after_first, after_second)
        self.assertIn("set -g status-bg colour234", after_second)

    def test_custom_history_limit_value(self):
        p = self._tmp()
        airuleset.apply_tmux_history_limit(p, limit=99999, run=lambda argv: None)
        self.assertIn("history-limit 99999", p.read_text())

    def test_default_run_invokes_real_tmux_binary_when_not_injected(self):
        # Smoke test only: no injected `run` -- the default path must shell
        # out to the real `tmux` binary and must never crash the whole
        # conf-file write regardless of live server state (a nonzero/failed
        # `tmux set-option` is asserted separately, with an injected fake,
        # by test_live_apply_nonzero_return_without_raising_is_logged). Both
        # SURVIVING managed options (#236 extended #235's history-limit-only
        # smoke test; #241 dropped window-size again) go through the real
        # `_default_tmux_run` path.
        p = self._tmp()
        changed = airuleset.apply_tmux_history_limit(p)
        self.assertTrue(changed)


class TestTmuxDestroyUnattached(TestCase):
    """#254: each attach to a grouped tmux session (zbynek-1..4, all
    sharing the same windows) spawned another grouped sibling that lingers
    detached forever under tmux's factory default `destroy-unattached off`
    -- reproduced live on dev1 against the real default socket with a
    genuine pty-attached-then-detached grouped sibling (see the STILL-VALID
    comment on #254). Fix: `destroy-unattached keep-last`.

    CORRECTION TO THE TICKET'S OWN SUGGESTED VALUE -- verified empirically,
    not assumed: the ticket's own title/body names `keep-group`, but a real
    pty-attached client on an isolated `-L` scratch tmux 3.7b server proved
    `keep-group` DESTROYS EVERY ORDINARY STANDALONE (non-grouped) session
    the moment its one client detaches -- identical to boolean `on`. Since
    almost every real project session on the fleet is a plain standalone
    session (never linked into a tmux session-group at all), shipping
    `keep-group` would nuke essentially every ordinary session on every
    detach -- a regression far worse than the bug being fixed. `keep-last`
    is the value that actually matches what the ticket's own prose
    describes ("destroy an unattached session ONLY when it is in a group
    and other sessions of the group remain"): it destroys a detached
    grouped sibling while >=1 other group member remains, and leaves BOTH
    the group's last survivor AND every standalone session completely
    untouched -- verified with a real 3-member group reduced cleanly to
    exactly 1 survivor, and a standalone session surviving an attach/
    detach cycle unharmed, both against a real running tmux 3.7b server.

    Live-apply is safe here for a DIFFERENT reason than history-limit's
    (#235) -- destroy-unattached, by definition, only ever evaluates
    sessions with ZERO attached clients, so it structurally cannot disturb
    anything currently on screen (unlike #236/#241's window-size, which
    recomputes the LIVE geometry of an ATTACHED client's window). Verified
    live: applying it against a running server holding a pre-existing
    pile-up (one attached session, two already-detached grouped
    duplicates -- the exact zbynek-1/2/3/4 shape before manual cleanup)
    immediately swept the two duplicates away with NO new attach/detach
    cycle needed, while leaving the attached grouped session AND a
    separate attached standalone session completely untouched. This is
    also the answer to "how do already-piled-up siblings get cleaned" --
    the live-apply itself performs a one-time sweep on the very next
    push/install; no new hook, no new watchdog job (respecting the
    FREEZE)."""

    def _tmp(self, content=None):
        d = tempfile.mkdtemp()
        p = Path(d) / ".tmux.conf"
        if content is not None:
            p.write_text(content)
        return p

    def test_conf_carries_destroy_unattached_keep_last(self):
        p = self._tmp()
        airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        text = p.read_text()
        self.assertIn("set-option -g destroy-unattached keep-last", text)

    def test_never_emits_keep_group_the_tickets_own_wrong_suggestion(self):
        # keep-group destroys every standalone session on detach (verified
        # live against a real tmux 3.7b server, see the class docstring) --
        # a regression lock so a future edit can never silently revert to
        # the ticket's own literally-named-but-wrong value.
        p = self._tmp()
        airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        text = p.read_text()
        self.assertNotIn("keep-group", text)

    def test_never_emits_bare_destroy_unattached_on(self):
        # boolean `on` has the SAME standalone-destruction problem as
        # keep-group, AND has no group-size awareness at all -- it would
        # destroy the group's own last surviving member too, defeating the
        # "zbynek-4 survives" acceptance criterion entirely.
        p = self._tmp()
        airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        text = p.read_text()
        self.assertNotIn("destroy-unattached on", text)

    def test_is_live_applied_alongside_history_limit(self):
        # Live-applying destroy-unattached is what self-heals an ALREADY-
        # existing pile-up on the very next push -- see the class
        # docstring for the live proof that this never touches an attached
        # session, grouped or standalone.
        p = self._tmp()
        calls = []
        airuleset.apply_tmux_history_limit(p, run=calls.append)
        self.assertIn(["tmux", "set-option", "-g", "destroy-unattached", "keep-last"], calls)

    def test_custom_destroy_unattached_value_is_injectable(self):
        # ADVERSARIAL-REVIEW FINDING (#254, MINOR): the first draft injected
        # "keep-last" -- the DEFAULT -- which cannot distinguish real
        # parameter threading from a hardcoded constant (a mutant reverting
        # BOTH consumption sites, the render call and the live argv, to the
        # module constant survived this test and 5 others). Inject a value
        # that DIFFERS from the default (mirroring the sibling
        # test_custom_history_limit_value's own limit=99999 != 50000
        # convention) and assert it reached BOTH the conf text and the
        # live-apply argv.
        p = self._tmp()
        calls = []
        airuleset.apply_tmux_history_limit(
            p, destroy_unattached="off", run=calls.append)
        self.assertIn("destroy-unattached off", p.read_text())
        self.assertIn(["tmux", "set-option", "-g", "destroy-unattached", "off"], calls)

    def test_idempotent_second_run_with_destroy_unattached_is_a_no_op(self):
        p = self._tmp("# my tmux conf\n")
        self.assertTrue(airuleset.apply_tmux_history_limit(p, run=lambda argv: None))
        before_text = p.read_text()
        changed = airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        self.assertFalse(changed)
        self.assertEqual(p.read_text(), before_text)


class TestTmuxWindowSizeRemoved(TestCase):
    """#241: `window-size manual` -- shipped fleet-wide by #236 -- CRASHES
    tmux 3.4's server outright at startup (`server exited unexpectedly`),
    confirmed live against the real 3.4 binary every managed box runs (the
    only version Ubuntu 24.04 noble ships). Unlike #236's own live-apply
    finding (flipping window-size against a RUNNING server snaps every
    window back to its stored size -- a disruptive resize, not a crash),
    this is a conf-READ-time failure with no safe way to keep shipping the
    option at all -- a box whose conf carries it cannot start tmux, full
    stop. So window-size is removed from the managed block ENTIRELY, at
    the source (render_tmux_history_block), not merely kept conf-only.
    default-size 176x50 is unaffected and stays -- it starts cleanly on
    3.4 and is what actually delivers #236's fixed-geometry goal for NEW
    windows; only history-limit is live-applied (exactly #235's original,
    already-proven-safe scope)."""

    def test_window_size_option_is_never_emitted_in_the_rendered_block(self):
        d = tempfile.mkdtemp()
        p = Path(d) / ".tmux.conf"
        airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        text = p.read_text()
        self.assertNotIn("window-size", text)
        # the surviving options are still both present -- this is a
        # targeted removal, not a regression of the whole feature.
        self.assertIn("set-option -g history-limit 50000", text)
        self.assertIn("set-option -g default-size 176x50", text)

    def test_default_size_never_reaches_a_live_run_call(self):
        d = tempfile.mkdtemp()
        p = Path(d) / ".tmux.conf"
        calls = []
        airuleset.apply_tmux_history_limit(p, run=calls.append)
        joined = " ".join(str(c) for c in calls)
        self.assertNotIn("default-size", joined)
        # but the conf file itself still carries it -- conf-only, not
        # dropped from the feature.
        self.assertIn("set-option -g default-size 176x50", p.read_text())

    def test_no_window_size_or_resize_shaped_live_call_is_ever_issued(self):
        # #267 widened the live-apply call count from 1 (history-limit
        # alone) to 4 (history-limit + the three Shift+PgUp/PgDn
        # `bind-key` calls, see TestTmuxHistoryLimit above) -- what THIS
        # class still locks is narrower and unaffected by that widening:
        # no call ever mentions window-size, resize-window or list-windows.
        p = Path(tempfile.mkdtemp()) / ".tmux.conf"
        calls = []
        airuleset.apply_tmux_history_limit(p, run=calls.append)
        joined = " ".join(" ".join(c) for c in calls)
        self.assertNotIn("window-size", joined)
        self.assertNotIn("resize-window", joined)
        self.assertNotIn("list-windows", joined)


class TestTmuxWindowSizeNoResize(TestCase):
    """#236's own incident history (two live-tmux destructions on dev1,
    the second a kernel segfault in tmux 3.4's format-expansion code)
    settled that `resize-window` is NEVER part of this feature -- not
    gated behind an attached-client check, not a "one-time final
    junction", not anywhere. Setting the default-size SERVER OPTION does
    not disturb any attached client's current window size; only
    `resize-window` does that, and it buys nothing new windows don't
    already get from `default-size` on their own. This is a structural,
    whole-file lock so it can never silently regress via a future edit
    anywhere in the module, not just inside the one function #236/#241
    touch."""

    def test_resize_window_is_never_constructed_or_invoked_anywhere(self):
        src = Path(airuleset.__file__).read_text()
        self.assertNotIn("resize-window", src)

    def test_list_windows_is_never_constructed_or_invoked_anywhere(self):
        src = Path(airuleset.__file__).read_text()
        self.assertNotIn("list-windows", src)


class TestTmuxScrollbackKeybinds(TestCase):
    """#267: the user rejected Ctrl+O (a live key-by-key test on the
    installed CC 2.1.223 found it is only an inline verbose toggle -- no
    pager, the documented PgUp/PgDn/{/}/[/] keys inside it do nothing at
    all) and the mouse-wheel-into-copy-mode default (awkward over ssh, no
    scroll wheel forwarding) -- and asked explicitly for the keyboard
    shortcut old Linux virtual consoles used: Shift+PageUp/PageDown.
    `TMUX_SCROLLBACK_KEYBINDS` is the single source of truth for the three
    `bind-key` argv lists, shared verbatim by both the rendered conf block
    and the live-apply calls (TestTmuxHistoryLimit above) -- this class
    locks that sharing so the two can never silently drift apart.

    #338: CC v2.1.226 shipped a NATIVE transcript viewer (Ctrl+O, reading
    the session's own clean internal history rather than the leaky tmux
    scrollback #267's own bind only ever scrolled INTO). The S-PageUp entry
    is now CONDITIONAL: inside a pane whose `pane_current_command` is
    literally `claude` it sends `C-o` (opens the native viewer -- PgUp/PgDn
    already work natively once it's open, no further wiring needed);
    everywhere else it falls through to the ORIGINAL, byte-identical
    `copy-mode -eu`. This is done via tmux's own `if -F` (alias of
    `if-shell -F`) conditional dispatch -- verified LIVE (not merely
    read from docs) via a real attached pty client feeding the real xterm
    CSI bytes for Shift+PageUp (`\\x1b[5;2~`) against two different real
    panes bound to the SAME key: a pane whose `pane_current_command` was
    `claude` (a real attached-pty fixture, `bash -c 'stty raw -echo; exec
    -a claude cat > <file>'` -- `stty raw -echo` is load-bearing, since a
    canonical-mode pty buffers a lone control byte with no trailing
    newline and never delivers it) received exactly one byte `0x0f`
    (Ctrl+O); a plain `sleep` pane on the SAME bind entered copy-mode
    (`#{pane_in_mode}` flipped 0->1), unchanged. `tmux send-keys -t <pane>`
    was deliberately never used to prove the BINDING itself -- it bypasses
    key-table dispatch entirely and writes straight into the pty, which
    proves nothing about whether a real keypress reaches the bound
    command (only a genuinely attached client's own input does). Also
    confirmed live: the rendered (`_tmux_conf_quote`d) conf line starts
    cleanly from a COLD conf file, and live-applies cleanly against an
    already-running server, on BOTH the fleet's real deployed
    `/usr/bin/tmux` (3.4) and `/usr/local/bin/tmux` (3.7b) -- no
    crash-at-parse-time hazard of the `window-size manual` (#241) kind.
    `S-NPage` (Shift+PageDown) is deliberately left untouched -- it has no
    existing root-table bind, and the native viewer's own PgDn already
    works once it's open."""

    def test_rendered_block_lines_are_built_from_the_shared_constant(self):
        # #338: the render path now applies `_tmux_conf_quote` per token
        # (never a bare `" ".join`) -- required the moment ANY argv entry
        # can contain a multi-word nested-command token (the new S-PageUp
        # entry's `"send-keys C-o"` / `"copy-mode -eu"`), matching the
        # quoting `popup_lines` (TMUX_POPUP_BIND_ARGVS) already uses.
        text = airuleset.render_tmux_history_block()
        for argv in airuleset.TMUX_SCROLLBACK_KEYBINDS:
            expected = " ".join(airuleset._tmux_conf_quote(tok) for tok in argv)
            self.assertIn(expected, text)

    def test_live_apply_argvs_are_built_from_the_shared_constant(self):
        p = Path(tempfile.mkdtemp()) / ".tmux.conf"
        calls = []
        airuleset.apply_tmux_history_limit(p, run=calls.append)
        # calls[0] is history-limit, calls[1] is #254's destroy-unattached
        # -- both plain set-option calls, neither part of the keybind list.
        # #289: the popup binds (TMUX_POPUP_BIND_ARGVS) are live-applied
        # AFTER the scrollback keybinds -- slice to exactly the scrollback
        # portion so this test stays scoped to TMUX_SCROLLBACK_KEYBINDS
        # alone (TestTmuxPopupBind below locks the popup portion). Live
        # apply passes real argv straight to subprocess -- no shell, no
        # quoting -- so this stays a plain equality check even for #338's
        # new multi-word-token entry.
        n = len(airuleset.TMUX_SCROLLBACK_KEYBINDS)
        keybind_calls = calls[2:2 + n]
        self.assertEqual(len(keybind_calls), n)
        for call, argv in zip(keybind_calls, airuleset.TMUX_SCROLLBACK_KEYBINDS):
            self.assertEqual(call, ["tmux"] + argv)

    def test_shift_pageup_dispatches_to_ctrl_o_in_claude_panes_else_copy_mode(self):
        # #338 replaces the old unconditional bind (locked here through
        # 8-8-2026) with a conditional `if -F` dispatch, live-verified (see
        # the class docstring) to evaluate PER KEYPRESS against the
        # CURRENT pane -- never once at conf-parse/bind time. The fallback
        # command stays the exact, byte-identical `copy-mode -eu` #267
        # shipped (auto-exit at the bottom, scroll up one page on entry),
        # so every non-claude pane's behaviour is completely unchanged.
        argv = airuleset.TMUX_SCROLLBACK_KEYBINDS[0]
        self.assertEqual(argv, [
            "bind-key", "-n", "S-PageUp", "if", "-F",
            "#{==:#{pane_current_command},claude}",
            "send-keys C-o", "copy-mode -eu",
        ])

    def test_shift_pagedown_is_bound_in_both_copy_mode_key_tables(self):
        # The managed conf pins neither `mode-keys` (vi vs emacs), so
        # Shift+PageDown must work regardless of which one a box/user ends
        # up on -- bound in BOTH `copy-mode` and `copy-mode-vi`. #338's own
        # S-PageUp entry uses `-n` (root table), not `-T`, so it is
        # structurally excluded from this filter without any change here.
        tables = {argv[2] for argv in airuleset.TMUX_SCROLLBACK_KEYBINDS
                  if argv[0] == "bind-key" and argv[1] == "-T"}
        self.assertEqual(tables, {"copy-mode", "copy-mode-vi"})

    def test_no_argv_element_contains_whitespace_except_the_nested_commands(self):
        # ADVERSARIAL-REVIEW FINDING (#267, MINOR -- F6) predicted exactly
        # this: "if a future keybind ever needed a quoted multi-word
        # argument ... lock it structurally rather than leave it to be
        # noticed later." #338 is that future keybind -- its `if -F`
        # dispatch needs TWO multi-word nested-command tokens
        # (`"send-keys C-o"`, `"copy-mode -eu"`) as single tmux argv
        # elements. The invariant survives, narrowed: every OTHER entry
        # (S-PageDown x2) still carries zero whitespace, and the new
        # entry's multi-word tokens are asserted PRESENT here (so a future
        # accidental re-split into separate argv elements is caught) --
        # their CORRECT quoting in the rendered conf line is locked
        # separately by test_multiword_scrollback_tokens_are_conf_quoted.
        s_pageup = airuleset.TMUX_SCROLLBACK_KEYBINDS[0]
        others = airuleset.TMUX_SCROLLBACK_KEYBINDS[1:]
        for argv in others:
            for token in argv:
                self.assertNotIn(" ", token, argv)
        multiword = [tok for tok in s_pageup if " " in tok]
        self.assertEqual(multiword, ["send-keys C-o", "copy-mode -eu"])

    def test_multiword_scrollback_tokens_are_conf_quoted(self):
        # The two multi-word S-PageUp nested-command tokens must survive
        # the conf render as SINGLE tmux words -- a bare, unquoted
        # `send-keys C-o` in the rendered line would parse as FOUR separate
        # tmux argv elements ("if", "-F", ..., "send-keys", "C-o", ...),
        # silently corrupting the `if -F` command's own argument count.
        text = airuleset.render_tmux_history_block()
        self.assertIn('"send-keys C-o"', text)
        self.assertIn('"copy-mode -eu"', text)


class TestTmuxConfQuote(TestCase):
    """`_tmux_conf_quote` renders a single conf-line WORD for tmux's own
    config parser -- unlike TMUX_SCROLLBACK_KEYBINDS' bare `" ".join`
    (locked as safe by TestTmuxScrollbackKeybinds' own
    test_no_argv_element_contains_whitespace above), the popup bind's argv
    contains `#{pane_current_path}`, whose literal `#` would start a tmux
    COMMENT if left unquoted at line-start -- quoting here is load-bearing
    for THAT character (see `test_format_string_token_is_quoted` below),
    not for embedded whitespace: none of TMUX_POPUP_BIND_ARGVS' own tokens
    actually contain a space (#289 adversarial-review M4)."""

    def test_simple_token_is_left_unquoted(self):
        for word in ("bind-key", "-n", "S-F1", "display-popup", "-E",
                     "90%", "claude-history"):
            self.assertEqual(airuleset._tmux_conf_quote(word), word)

    def test_token_with_whitespace_is_double_quoted(self):
        self.assertEqual(airuleset._tmux_conf_quote("a b"), '"a b"')

    def test_token_with_semicolon_is_quoted(self):
        # A bare `;` is a tmux command separator outside quotes.
        self.assertEqual(airuleset._tmux_conf_quote("a;b"), '"a;b"')

    def test_embedded_double_quote_is_escaped(self):
        self.assertEqual(airuleset._tmux_conf_quote('a "b" c'), '"a \\"b\\" c"')

    def test_embedded_backslash_is_escaped(self):
        self.assertEqual(airuleset._tmux_conf_quote("a\\b c"), '"a\\\\b c"')

    def test_bare_backslash_with_no_space_is_still_quoted_and_escaped(self):
        # ADVERSARIAL-REVIEW FINDING (#289, M3): the sibling test above
        # (`test_embedded_backslash_is_escaped`) always pairs its
        # backslash with a space in the SAME fixture, so a mutant dropping
        # `\\` from the trigger regex's character class still survives --
        # the space alone is enough to trigger quoting, and the escape
        # then happens regardless of whether `\\` is actually IN the
        # trigger class. This fixture isolates the backslash as the ONLY
        # thing that could possibly trigger quoting.
        self.assertEqual(airuleset._tmux_conf_quote("a\\b"), '"a\\\\b"')

    def test_format_string_token_is_quoted(self):
        # `#{pane_current_path}` contains no whitespace/quote/semicolon/
        # backslash/hash-at-start... but DOES contain a literal '#' which
        # would start a tmux COMMENT if it were the first char of an
        # unquoted word at line start; mid-word it's harmless, but this
        # function conservatively quotes anything containing '#' too.
        self.assertEqual(airuleset._tmux_conf_quote("#{pane_current_path}"),
                          '"#{pane_current_path}"')

    def test_token_with_single_quote_is_double_quoted(self):
        # ADVERSARIAL-REVIEW FINDING (#289, M2): an UNQUOTED `'` mid-word
        # starts real single-quote mode in tmux's own conf-parser grammar
        # too (not just at the start of a bare word) -- left unquoted, a
        # word containing one can swallow the rest of the conf file as
        # single-quoted text. A single quote needs no ESCAPING inside a
        # tmux double-quoted string (verified live), but it DOES need to
        # TRIGGER quoting when the word is otherwise bare.
        self.assertEqual(airuleset._tmux_conf_quote("a'b"), '"a\'b"')

    def test_dollar_sign_is_refused_not_silently_mis_rendered(self):
        # ADVERSARIAL-REVIEW FINDING (#289, M1): tmux's own conf-parser
        # expands $VAR at conf-parse/bind time -- both quoted and
        # unquoted -- so no quoting form here can protect a literal '$'.
        # The function refuses rather than silently rendering something
        # that will be corrupted at bind time (the exact class of bug
        # this ticket self-found and fixed for the popup's own command).
        with self.assertRaises(ValueError):
            airuleset._tmux_conf_quote("$HOME")

    def test_empty_string_is_quoted_not_dropped(self):
        # An empty token must still render as SOMETHING (a real tmux word
        # boundary), never silently vanish from the joined conf line.
        self.assertEqual(airuleset._tmux_conf_quote(""), '""')


class TestTmuxPopupBind(TestCase):
    """#289: a one-keystroke POPUP over `claude-history` (#267's
    companion) -- the discoverability gap the ticket was reopened over
    (claude-history existed but nobody knew to type it). #376 REMOVES
    S-F1 (never confirmed to reach the user's real terminal/ssh client)
    and S-DC (confirmed delivered but explicitly downgraded by the
    user's own binding correction -- a guaranteed shortcut must be
    prefix-class only) -- prefix-h is the ONE surviving binding, the
    only one the user personally confirmed opens. See the module comment
    above TMUX_POPUP_PREFIX_KEY in airuleset.py for the full design
    rationale, including the live-verified reason the invoked command is
    a SEPARATE SCRIPT FILE (CLAUDE_HISTORY_POPUP_SCRIPT_DEST) rather
    than an inline shell command: tmux's own conf-file DOUBLE-QUOTE
    parser expands `$VAR` at bind time using tmux's OWN process
    environment, silently blanking any shell-runtime variable
    (`$CH_OUT`/`$CH_RC`/`$?`) that doesn't exist there."""

    def test_prefix_fallback_is_h(self):
        self.assertEqual(airuleset.TMUX_POPUP_PREFIX_KEY, "h")

    def test_exactly_one_binding_survives(self):
        # #376: S-F1/S-DC removed -- TMUX_POPUP_BIND_ARGVS has exactly
        # one entry now.
        self.assertEqual(len(airuleset.TMUX_POPUP_BIND_ARGVS), 1)

    def test_prefix_table_bind_has_no_dash_n(self):
        argv = airuleset.TMUX_POPUP_BIND_ARGVS[0]
        self.assertEqual(argv[:2], ["bind-key", "h"])
        self.assertNotIn("-n", argv)

    def test_bind_invokes_display_popup_with_the_script_path(self):
        for argv in airuleset.TMUX_POPUP_BIND_ARGVS:
            self.assertIn("display-popup", argv)
            self.assertIn(str(airuleset.CLAUDE_HISTORY_POPUP_SCRIPT_DEST), argv)

    def test_popup_invokes_the_script_by_its_own_absolute_path(self):
        # NEVER an inline shell command (the landmine this ticket's own
        # live verification found) and NEVER the `claude-history` bashrc
        # FUNCTION (display-popup runs non-interactively -- ~/.bashrc is
        # never sourced).
        for argv in airuleset.TMUX_POPUP_BIND_ARGVS:
            cmd = argv[-1]
            self.assertEqual(cmd, str(airuleset.CLAUDE_HISTORY_POPUP_SCRIPT_DEST))
            self.assertTrue(Path(cmd).is_absolute())
            self.assertNotIn("$", cmd)
            self.assertNotIn(";", cmd)

    def test_popup_sizes_generously_and_titles_itself(self):
        # #376: bumped 90% -> 96% while the wrap fix (--width) is what
        # actually solves horizontal-scroll readability -- a modest,
        # near-edge-to-edge width is still worth the free real estate.
        for argv in airuleset.TMUX_POPUP_BIND_ARGVS:
            self.assertIn("-w", argv)
            self.assertIn("96%", argv)
            self.assertIn("-h", argv)
            self.assertIn("-T", argv)
            self.assertIn("claude-history", argv)

    def test_popup_starts_in_the_originating_panes_own_cwd(self):
        # `-d '#{pane_current_path}'` -- format-EXPANDED by tmux (unlike
        # the shell-command argument, verified live) -- so claude-history's
        # own `--cwd` default (os.getcwd()) resolves the right project
        # with no `--pane` argument needed.
        for argv in airuleset.TMUX_POPUP_BIND_ARGVS:
            self.assertIn("-d", argv)
            self.assertIn("#{pane_current_path}", argv)

    def test_popup_closes_on_the_scripts_own_exit(self):
        # Plain `-E` (not `-EE`): the script itself already waits for a
        # keypress on its OWN failure branch before exiting, so tmux can
        # close the popup unconditionally once the script returns.
        for argv in airuleset.TMUX_POPUP_BIND_ARGVS:
            self.assertIn("-E", argv)
            self.assertNotIn("-EE", argv)

    def test_rendered_conf_line_contains_the_bind(self):
        text = airuleset.render_tmux_history_block()
        self.assertIn("bind-key h display-popup", text)
        self.assertIn(str(airuleset.CLAUDE_HISTORY_POPUP_SCRIPT_DEST), text)
        self.assertNotIn("S-F1", text)
        self.assertNotIn("S-DC", text)

    def test_no_dollar_sign_survives_into_the_rendered_conf_line(self):
        # ADVERSARIAL-REVIEW-CLASS FINDING (self-caught, #289): an earlier
        # design embedded `$CH_OUT`/`$CH_RC`/`$?` directly in the popup's
        # shell-command argument -- tmux's OWN conf-parser silently
        # expanded/blanked them at bind time (verified live). The fixed
        # design's rendered conf line must carry NO `$` at all -- the
        # script path is a plain absolute path, and `#{pane_current_path}`
        # is a tmux FORMAT string, not a shell variable.
        text = airuleset.render_tmux_history_block()
        popup_lines = [ln for ln in text.splitlines() if "display-popup" in ln]
        self.assertEqual(len(popup_lines), 1)
        for line in popup_lines:
            self.assertNotIn("$", line)

    def test_no_mode_plumbing_survives_the_removal(self):
        # #376: the mode-branching machinery (#337) is fully gone -- no
        # constant, no `-e` flag, no AIRULESET_POPUP_MODE anywhere in the
        # rendered conf or the argv.
        self.assertFalse(hasattr(airuleset, "TMUX_POPUP_MODE_TRANSCRIPT_PRIMARY"))
        self.assertFalse(hasattr(airuleset, "TMUX_POPUP_KEY"))
        self.assertFalse(hasattr(airuleset, "TMUX_POPUP_KEY_ALT"))
        for argv in airuleset.TMUX_POPUP_BIND_ARGVS:
            self.assertNotIn("-e", argv)
            self.assertNotIn("AIRULESET_POPUP_MODE", " ".join(argv))
        text = airuleset.render_tmux_history_block()
        self.assertNotIn("AIRULESET_POPUP_MODE", text)


class TestClaudeHistoryPopupScript(TestCase):
    """CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT -- the standalone script the
    popup bind invokes by path (see TestTmuxPopupBind above for why it
    is a separate file, never an inline shell command). #376 makes the
    complete, hole-free transcript reconstruction UNCONDITIONALLY
    PRIMARY (no more MODE branching -- S-F1/S-DC are gone, prefix-h is
    the only binding), with a real `tmux capture-pane` as its OWN
    fallback only when the reconstruction itself resolves nothing."""

    def test_starts_with_bash_shebang_and_set_euo_pipefail(self):
        content = airuleset.render_claude_history_popup_script()
        self.assertTrue(content.startswith("#!/usr/bin/env bash"))
        self.assertIn("set -euo pipefail", content)

    def test_reads_terminal_width_via_tput_cols(self):
        # #376: the reconstruction is rendered word-wrapped to the
        # popup's own live column width -- no horizontal scrolling. A
        # `tput cols` failure (no controlling terminal) must not abort
        # the script under `set -e` -- WIDTH degrades to 0 (claude-
        # history's own --width contract: 0/omitted is a no-op).
        content = airuleset.render_claude_history_popup_script()
        self.assertIn('WIDTH="$(tput cols 2>/dev/null)" || WIDTH=0', content)
        self.assertIn('--width "$WIDTH"', content)

    def test_captures_exit_code_without_the_set_e_assignment_trap(self):
        # `set -e` + `VAR=$(failing_cmd)` exits the script BEFORE the next
        # line ever runs (this repo's own documented gotcha) -- the fix is
        # `VAR=$(cmd) || RC=$?` on ONE line, never a bare `VAR=$(cmd)`
        # followed by a SEPARATE, unguarded `RC=$?` line (which `set -e`
        # would never even reach on a real failure).
        content = airuleset.render_claude_history_popup_script()
        assign_line = next(ln for ln in content.splitlines() if "CH_OUT=$(" in ln)
        self.assertIn("|| CH_RC=$?", assign_line, assign_line)
        # The bare (dangerous) shape would be its OWN separate statement
        # line consisting of exactly `CH_RC=$?` with no `||` before it.
        self.assertNotIn("CH_RC=$?", [ln.strip() for ln in content.splitlines()])

    def test_every_command_substitution_assignment_uses_the_safe_set_e_form(self):
        # Iterates EVERY `=$(` assignment in the whole rendered script and
        # requires each one to use the safe `|| <NAME>_RC=$?` form -- a
        # mutant dropping the guard from just ONE would survive a
        # `next()`-based check unnoticed.
        content = airuleset.render_claude_history_popup_script()
        # `.strip().startswith("#")` excludes this script's own COMMENTS.
        assign_lines = [ln for ln in content.splitlines()
                        if "=$(" in ln and not ln.strip().startswith("#")]
        self.assertGreaterEqual(len(assign_lines), 2, assign_lines)
        for ln in assign_lines:
            self.assertRegex(ln, r"\|\| \w+_RC=\$\?", ln)

    def test_pipes_success_output_into_less_dash_r_plus_g_exactly_once(self):
        # #294: -R added so `less` renders raw ANSI color bytes as color
        # instead of visibly escaping them; +G (jump to end) and less's
        # own default incremental search are both unaffected by -R.
        # #376: the script no longer branches into two separate success
        # paths (#327's capture-pane-primary `exit 0` early-return is
        # gone) -- there is exactly ONE `less -R +G` pipe now, at the
        # bottom, shared regardless of which source resolved CH_OUT.
        content = airuleset.render_claude_history_popup_script()
        self.assertEqual(content.count("| less -R +G"), 1, content)

    def test_transcript_reconstruction_is_unconditionally_primary(self):
        # #376: no more MODE branching -- the FIRST real command in the
        # script (after the WIDTH read) is claude-history --full --color,
        # forced --color unconditionally (never --plain -- that was
        # #327's now-reversed fallback-neutrality choice).
        content = airuleset.render_claude_history_popup_script()
        lines = content.splitlines()
        ch_idx = next(i for i, ln in enumerate(lines) if "CH_OUT=$(" in ln)
        self.assertIn("airuleset-claude-history.py", lines[ch_idx])
        self.assertIn("--full", lines[ch_idx], lines[ch_idx])
        self.assertIn("--color", lines[ch_idx], lines[ch_idx])
        self.assertNotIn("--plain", lines[ch_idx], lines[ch_idx])
        # and it is the FIRST assignment in the file (before any
        # capture-pane call), proving primacy structurally, not just by
        # the word "primary" in a comment.
        cp_idx = next(i for i, ln in enumerate(lines) if "CP_OUT=$(" in ln)
        self.assertLess(ch_idx, cp_idx,
                         "the transcript reconstruction must be attempted "
                         "BEFORE the capture-pane fallback")

    def test_no_mode_variable_survives(self):
        # #376 removed the whole per-BINDING MODE if/else that used to
        # select between S-DC (transcript-primary) and S-F1/prefix-h
        # (capture-pane-primary) -- there is only ONE remaining `else`
        # in the script now, and it is the M5 both-sources-failed guard
        # nested INSIDE the capture-pane fallback's own if/else, not a
        # top-level mode selector.
        content = airuleset.render_claude_history_popup_script()
        self.assertNotIn("AIRULESET_POPUP_MODE", content)
        self.assertNotIn('MODE="', content)
        lines = [ln.strip() for ln in content.splitlines()]
        # two legitimate `else`s remain: the M5 both-sources-failed
        # guard, and the missing-less/success split at the bottom --
        # neither is a per-BINDING mode selector.
        self.assertEqual(lines.count("else"), 2, lines)

    def test_capture_pane_fallback_has_no_explicit_target(self):
        # LOAD-BEARING: a bare (no `-t`) capture-pane call issued from
        # WITHIN a display-popup's own shell-command resolves against the
        # ORIGINATING pane (live-verified, twice, independently -- see the
        # module comment above CLAUDE_HISTORY_POPUP_SCRIPT_DEST). Adding
        # `-t` here would break that resolution outright.
        content = airuleset.render_claude_history_popup_script()
        cp_line = next(ln for ln in content.splitlines()
                       if "CP_OUT=$(tmux capture-pane" in ln)
        self.assertNotIn(" -t ", cp_line, cp_line)
        self.assertNotIn(" -t", cp_line, cp_line)
        self.assertIn(" -e ", cp_line, cp_line)
        self.assertIn(" -p ", cp_line, cp_line)

    def test_capture_pane_uses_the_configured_history_limit(self):
        content = airuleset.render_claude_history_popup_script()
        self.assertIn("-S -%d" % airuleset.TMUX_HISTORY_LIMIT, content)

    def test_capture_pane_history_limit_is_wired_not_hardcoded(self):
        # ADVERSARIAL-REVIEW-CLASS FINDING this repo's own playbook
        # already documents for the sibling #327 ticket: a hardcoded
        # `-S -50000` literal passes even a test that only checks the
        # DEFAULT value -- rendering with a DIFFERENT limit must produce
        # a DIFFERENT `-S` value, proving it is genuinely parameterized.
        default_content = airuleset.render_claude_history_popup_script()
        self.assertIn("-S -%d" % airuleset.TMUX_HISTORY_LIMIT, default_content)
        custom = airuleset.render_claude_history_popup_script(limit=12345)
        self.assertIn("-S -12345", custom)
        self.assertNotIn("-S -%d" % airuleset.TMUX_HISTORY_LIMIT, custom)
        self.assertNotIn("{{TMUX_HISTORY_LIMIT}}", custom)

    def test_fallback_only_triggers_on_failure_or_empty_transcript_output(self):
        content = airuleset.render_claude_history_popup_script()
        # The fallback lives inside a guard testing BOTH the primary
        # attempt's exit code and its emptiness -- never RC alone (that
        # would miss the real "rc=0 but $(...) stripped to empty" case).
        self.assertIn('[ "$CH_RC" -ne 0 ] || [ -z "$CH_OUT" ]', content)

    def test_both_sources_failing_produces_the_m5_dual_diagnostic(self):
        # M5 guard: BOTH sources genuinely failed/produced nothing --
        # fail loudly with both diagnostics shown, never a silent
        # instant-close.
        content = airuleset.render_claude_history_popup_script()
        self.assertIn("produced nothing", content)
        self.assertIn("also produced nothing", content)

    def test_failure_branch_waits_for_a_keypress_before_closing(self):
        # No silent instant-close: on a nonzero exit, the script prints
        # the error and waits for input rather than handing `less` an
        # empty pipe.
        content = airuleset.render_claude_history_popup_script()
        self.assertIn("press any key to close", content)
        self.assertIn("read -n 1", content)

    def test_missing_less_guard_present(self):
        content = airuleset.render_claude_history_popup_script()
        self.assertIn('"less" is not installed', content)

    def test_prints_a_loading_message_before_the_slow_transcript_read(self):
        # ADVERSARIAL-REVIEW FINDING (#376, M1): measured live against this
        # repo's own real project data, the transcript reconstruction alone
        # can take ~25s / ~800MB peak RSS -- with nothing printed first, the
        # popup appears BLANK/frozen for that whole window (a real
        # regression this ticket's own review demanded be fixed, not just
        # documented). A stderr line printed BEFORE the slow `CH_OUT=$(...)`
        # capture starts is the minimal fix the review itself suggested.
        content = airuleset.render_claude_history_popup_script()
        self.assertIn("Loading claude-history", content)
        lines = content.splitlines()
        loading_idx = next(i for i, ln in enumerate(lines)
                            if "Loading claude-history" in ln)
        ch_idx = next(i for i, ln in enumerate(lines) if "CH_OUT=$(" in ln)
        self.assertLess(loading_idx, ch_idx,
                         "the loading message must print BEFORE the slow "
                         "transcript-reconstruction capture starts, or the "
                         "popup still appears blank/frozen during it")
        # Printed to STDERR specifically -- never mixed into the `$( )`
        # command-substitution's own captured stdout, and never into the
        # final content `less` renders.
        loading_line = lines[loading_idx]
        self.assertIn(">&2", loading_line, loading_line)

    # -- real execution (genuine `bash` subprocess, not a mock) --

    def _deploy(self, home):
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        hscript = claude_dir / airuleset.CLAUDE_HISTORY_SCRIPT_DEST.name
        hscript.write_text(airuleset.render_claude_history_script())
        os.chmod(hscript, 0o755)
        pscript = claude_dir / airuleset.CLAUDE_HISTORY_POPUP_SCRIPT_DEST.name
        pscript.write_text(airuleset.render_claude_history_popup_script())
        os.chmod(pscript, 0o755)
        return pscript

    def _env_no_real_tmux(self, home):
        """A subprocess env for the CAPTURE-PANE FALLBACK tests below:
        inherits the real PATH (so bash/python3/less resolve) but NEVER
        the real $TMUX -- this box's own agent-strip tmux server IS a
        genuine, reachable server, and inheriting its $TMUX would make
        `tmux capture-pane` silently SUCCEED against it. TMUX_TMPDIR
        points at a directory with NO server listening, so `tmux
        capture-pane` fails cleanly and deterministically."""
        no_server_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(no_server_dir), True)
        return {"HOME": str(home), "PATH": os.environ.get("PATH", ""),
                "TMUX_TMPDIR": str(no_server_dir)}

    def _seed_transcript(self, home, cwd, marker):
        enc = airuleset.encode_project_dir(str(cwd))
        proj_dir = home / ".claude" / "projects" / enc
        proj_dir.mkdir(parents=True)
        lines = [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": marker}]}},
        ]
        (proj_dir / "sess.jsonl").write_text(
            "\n".join(json.dumps(x) for x in lines) + "\n")

    def test_real_execution_transcript_primary_success_path(self):
        # Genuine `bash` execution against a real deployed claude-history
        # script + a real seeded transcript, with capture-pane forced to
        # fail (see _env_no_real_tmux) -- so a clean success here is
        # direct proof the transcript reconstruction alone answered it,
        # unconditionally, with NO env var required at all (#376 removed
        # AIRULESET_POPUP_MODE entirely -- there is nothing left to set).
        home = Path(tempfile.mkdtemp())
        pscript = self._deploy(home)
        cwd = home / "proj"
        cwd.mkdir()
        self._seed_transcript(home, cwd, "TRANSCRIPT-PRIMARY-MARKER-376")
        env = self._env_no_real_tmux(home)
        r = subprocess.run(["bash", str(pscript)], cwd=str(cwd), env=env,
                            capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0)
        self.assertIn("TRANSCRIPT-PRIMARY-MARKER-376", r.stdout)
        self.assertNotIn("also produced nothing", r.stdout)
        # #376 M1: the loading message reaches STDERR on a real run, and
        # never leaks into stdout (which `less` renders as the final
        # transcript content -- a leaked loading line there would show up
        # as visible junk at the top of every real popup).
        self.assertIn("Loading claude-history", r.stderr)
        self.assertNotIn("Loading claude-history", r.stdout)

    def _spawn_isolated_tmux_pane(self, session_argv, scratch):
        """A throwaway tmux server on an isolated TMUX_TMPDIR -- NEVER
        this worker's own real $TMUX (confirmed live: this Bash tool's
        own process environment carries a genuine $TMUX pointing at a
        live session on this box), so env is built EXPLICITLY here,
        never `{**os.environ}`, and the "TMUX" key is never included at
        all. `session_argv` runs directly as the session's own command."""
        env = {"TMUX_TMPDIR": scratch, "PATH": "/usr/local/bin:/usr/bin:/bin"}
        r = subprocess.run(
            ["tmux", "new-session", "-d", "-s", "t376", "-x", "80", "-y", "24"]
            + session_argv,
            env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.addCleanup(subprocess.run, ["tmux", "kill-server"], env=env,
                         capture_output=True, text=True, timeout=10)
        return env

    def test_real_execution_falls_back_to_real_capture_pane(self):
        # No transcript for this cwd (claude-history fails) -- falls back
        # to a REAL `tmux capture-pane` of the originating pane, spun up
        # on an isolated throwaway server.
        home = Path(tempfile.mkdtemp())
        pscript = self._deploy(home)
        cwd = home / "proj"
        cwd.mkdir()
        marker = "CAPTURE-PANE-FALLBACK-MARKER-376"
        scratch = tempfile.mkdtemp()
        self._spawn_isolated_tmux_pane(
            ["bash", "-c", "echo %s; sleep 60" % marker], scratch)
        env = {"TMUX_TMPDIR": scratch, "PATH": "/usr/local/bin:/usr/bin:/bin",
               "HOME": str(home)}
        r = subprocess.run(["bash", str(pscript)], cwd=str(cwd), env=env,
                            capture_output=True, text=True, timeout=15, input="")
        self.assertEqual(r.returncode, 0)
        self.assertIn(marker, r.stdout)
        self.assertNotIn("also produced nothing", r.stdout)

    def test_real_execution_falls_back_when_claude_history_exits_zero_but_empty(self):
        # Real-execution proof for the OTHER half of the fallback trigger
        # guard (`[ "$CH_RC" -ne 0 ] || [ -z "$CH_OUT" ]`). The REAL
        # claude-history script can never actually produce "rc=0, empty
        # stdout" on its own (a genuine success always prints at least a
        # header line), so this substitutes a trivial stand-in AT THE
        # EXACT SAME invocation path the popup script shells out to --
        # then proves the REAL fallback still fires end-to-end via a
        # real tmux capture-pane read.
        home = Path(tempfile.mkdtemp())
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True)
        hscript = claude_dir / airuleset.CLAUDE_HISTORY_SCRIPT_DEST.name
        hscript.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
        os.chmod(hscript, 0o755)
        pscript = claude_dir / airuleset.CLAUDE_HISTORY_POPUP_SCRIPT_DEST.name
        pscript.write_text(airuleset.render_claude_history_popup_script())
        os.chmod(pscript, 0o755)
        cwd = home / "proj"
        cwd.mkdir()
        marker = "RC0-EMPTY-FALLBACK-MARKER-376"
        scratch = tempfile.mkdtemp()
        self._spawn_isolated_tmux_pane(
            ["bash", "-c", "echo %s; sleep 60" % marker], scratch)
        env = {"TMUX_TMPDIR": scratch, "PATH": "/usr/local/bin:/usr/bin:/bin",
               "HOME": str(home)}
        r = subprocess.run(["bash", str(pscript)], cwd=str(cwd), env=env,
                            capture_output=True, text=True, timeout=15, input="")
        self.assertEqual(r.returncode, 0)
        self.assertIn(marker, r.stdout)
        self.assertNotIn("also produced nothing", r.stdout)

    def test_real_execution_fails_loudly_when_capture_pane_is_blank_too(self):
        # A pane running `sleep` DIRECTLY (never a shell) renders
        # deterministically blank -- capture-pane succeeds (rc=0) but
        # $(...) strips it to an empty string, and the M5 FAILS-LOUDLY
        # guard must still trigger (both sources genuinely produced
        # nothing), never a silent instant-close.
        home = Path(tempfile.mkdtemp())
        pscript = self._deploy(home)
        cwd = home / "proj"
        cwd.mkdir()
        scratch = tempfile.mkdtemp()
        self._spawn_isolated_tmux_pane(["sleep", "60"], scratch)
        env = {"TMUX_TMPDIR": scratch, "PATH": "/usr/local/bin:/usr/bin:/bin",
               "HOME": str(home)}
        r = subprocess.run(["bash", str(pscript)], cwd=str(cwd), env=env,
                            capture_output=True, text=True, timeout=15, input="")
        self.assertEqual(r.returncode, 0)  # the || true guard absorbs read's EOF
        self.assertIn("press any key to close", r.stdout)
        self.assertIn("also produced nothing", r.stdout)

    def test_real_execution_fails_loudly_when_neither_source_works(self):
        # No transcript, and no reachable tmux server at all (an empty,
        # real TMUX_TMPDIR with no socket ever created in it) -- both
        # sources genuinely fail; the SAME shared M5 guard must fire.
        home = Path(tempfile.mkdtemp())
        pscript = self._deploy(home)
        cwd = home / "proj"
        cwd.mkdir()
        unreachable = tempfile.mkdtemp()
        env = {"TMUX_TMPDIR": unreachable, "PATH": "/usr/local/bin:/usr/bin:/bin",
               "HOME": str(home)}
        r = subprocess.run(["bash", str(pscript)], cwd=str(cwd), env=env,
                            capture_output=True, text=True, timeout=15, input="")
        self.assertEqual(r.returncode, 0)
        self.assertIn("press any key to close", r.stdout)
        self.assertIn("also produced nothing", r.stdout)

    def test_real_execution_missing_less_shows_transcript_then_waits(self):
        # ADVERSARIAL-REVIEW FINDING (#289, M5): a box genuinely missing
        # `less` must fail LOUDLY (show the transcript + wait for a
        # keypress) rather than handing a nonexistent command the
        # successfully-read output and instant-closing silently.
        home = Path(tempfile.mkdtemp())
        pscript = self._deploy(home)
        cwd = home / "proj"
        cwd.mkdir()
        self._seed_transcript(home, cwd, "MISSING-LESS-MARKER-376")
        # A PATH containing ONLY symlinks to python3/bash -- no `less`,
        # no `tmux` -- reproduces "less is not installed" and forces the
        # capture-pane fallback attempt to fail with "command not found"
        # (never reaching THIS box's own real tmux server).
        narrow_bin = Path(tempfile.mkdtemp())
        for tool in ("python3", "bash"):
            real_tool = shutil.which(tool)
            os.symlink(real_tool, narrow_bin / tool)
        no_server_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(no_server_dir), True)
        env = {"HOME": str(home), "PATH": str(narrow_bin),
               "TMUX_TMPDIR": str(no_server_dir)}
        r = subprocess.run(["bash", str(pscript)], cwd=str(cwd), env=env,
                            capture_output=True, text=True, timeout=15, input="")
        self.assertEqual(r.returncode, 0)  # the || true guard absorbs read's EOF
        self.assertIn("MISSING-LESS-MARKER-376", r.stdout)
        self.assertIn('"less" is not installed', r.stdout)
        self.assertIn("press any key to close", r.stdout)

class TestApplyUltracodeLauncherDeploysPopupScript(TestCase):
    """apply_ultracode_launcher (#289 extension) must deploy the popup
    script with the SAME unconditional-write + chmod +x + missing-after-
    write-RuntimeError treatment as the sibling launch/history scripts
    (#77/#267) -- see TestUltracodeLauncher above for those siblings'
    own dedicated locks; this class covers only the NEW popup-script
    behavior to avoid duplicating that whole class."""

    def _tmp(self):
        d = Path(tempfile.mkdtemp())
        return (d / ".bashrc", d / ".claude" / "airuleset-claude-launch.sh",
                d / ".claude" / "airuleset-claude-history.py",
                d / ".claude" / "airuleset-claude-history-popup.sh")

    def test_popup_script_is_written_and_executable(self):
        p, s, h, pp = self._tmp()
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        self.assertTrue(pp.exists())
        self.assertEqual(pp.read_text(), airuleset.render_claude_history_popup_script())
        self.assertTrue(os.access(pp, os.X_OK))

    def test_popup_script_rewritten_unconditionally_every_call(self):
        p, s, h, pp = self._tmp()
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        pp.write_text("BROKEN")
        airuleset.apply_ultracode_launcher(p, s, h, pp)
        self.assertEqual(pp.read_text(), airuleset.render_claude_history_popup_script())

    def test_missing_popup_script_after_write_raises(self):
        # Simulate a write that silently didn't land (write_text is a
        # no-op for `pp` only) -- os.chmod is ALSO no-op'd, since the real
        # code calls it before the existence check and chmod on a
        # genuinely-missing path would raise its OWN (unrelated)
        # FileNotFoundError first, masking the RuntimeError under test.
        p, s, h, pp = self._tmp()
        real_write_text = Path.write_text
        real_chmod = os.chmod

        def _write_boom(self, *a, **kw):
            if self == pp:
                return  # simulate a write that silently didn't land
            return real_write_text(self, *a, **kw)

        def _chmod_noop(path, mode):
            if str(path) == str(pp):
                return
            return real_chmod(path, mode)

        with m.patch.object(Path, "write_text", _write_boom), \
                m.patch.object(os, "chmod", _chmod_noop):
            with self.assertRaises(RuntimeError):
                airuleset.apply_ultracode_launcher(p, s, h, pp)


class _FakeCP:
    """A subprocess.CompletedProcess stand-in -- just returncode/stdout/stderr."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestTmuxCutoverScriptContent(TestCase):
    """#242: the inline boot-time cutover script (TMUX_CUTOVER_SCRIPT_CONTENT,
    same "rendered unconditionally, no separate template file" shape as
    apply_ultracode_launcher's own CLAUDE_LAUNCH_SCRIPT_CONTENT) must compare
    the current /usr/local/bin/tmux symlink target against tmux-3.7b and only
    re-link when it differs -- a true no-op once correct -- and must NEVER
    reference the packaged /usr/bin/tmux anywhere."""

    def test_starts_with_posix_shebang_and_fails_loudly(self):
        self.assertTrue(airuleset.TMUX_CUTOVER_SCRIPT_CONTENT.startswith("#!/bin/sh"))
        self.assertIn("set -eu", airuleset.TMUX_CUTOVER_SCRIPT_CONTENT)

    def test_never_references_the_packaged_binary(self):
        self.assertNotIn("/usr/bin/tmux", airuleset.TMUX_CUTOVER_SCRIPT_CONTENT)

    def test_carries_the_managed_newest_and_target_paths(self):
        self.assertIn(airuleset.TMUX_CUTOVER_NEWEST, airuleset.TMUX_CUTOVER_SCRIPT_CONTENT)
        self.assertIn("/usr/local/bin/tmux", airuleset.TMUX_CUTOVER_SCRIPT_CONTENT)

    def test_compares_before_relinking_no_unconditional_ln(self):
        # A structural guard: `ln -sfn` must be gated behind the
        # CURRENT-vs-NEWEST comparison, never issued unconditionally.
        self.assertIn('if [ "$CURRENT" != "$NEWEST" ]', airuleset.TMUX_CUTOVER_SCRIPT_CONTENT)
        self.assertIn("ln -sfn", airuleset.TMUX_CUTOVER_SCRIPT_CONTENT)

    def test_exits_early_when_newest_build_is_missing_or_not_executable(self):
        # -x, not -e: a present-but-non-executable NEWEST (a truncated /
        # interrupted copy, wrong perms) must never become the boot-time
        # target either (adversarial-review finding, #242).
        self.assertIn('if [ ! -x "$NEWEST" ]', airuleset.TMUX_CUTOVER_SCRIPT_CONTENT)
        self.assertNotIn('if [ ! -e "$NEWEST" ]', airuleset.TMUX_CUTOVER_SCRIPT_CONTENT)


class TestTmuxCutoverScriptRealBehavior(TestCase):
    """Genuine `sh` execution of the shipped script content against a
    throwaway sandbox, via the AIRULESET_TMUX_CUTOVER_NEWEST/TARGET env-var
    overrides the script reads (unset in production -- the hardcoded
    defaults always apply there). Proves the LOGIC, not just the text."""

    def _tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _fake_tmux(self, path, executable=True):
        path.write_text("fake binary")
        path.chmod(0o755 if executable else 0o644)
        return path

    def _run_script(self, env_extra):
        d = self._tmp()
        script = Path(d) / "cutover.sh"
        script.write_text(airuleset.TMUX_CUTOVER_SCRIPT_CONTENT)
        script.chmod(0o755)
        env = {**os.environ, **env_extra}
        r = subprocess.run(["sh", str(script)], capture_output=True, text=True, env=env)
        return d, r

    def test_newest_missing_leaves_target_untouched(self):
        d = self._tmp()
        newest = Path(d) / "no-such-tmux-3.7b"          # deliberately never created
        target = Path(d) / "tmux"
        packaged = Path(d) / "tmux-packaged"
        packaged.write_text("old packaged binary")
        target.symlink_to(packaged)                      # simulates dev2/gk/subdev today
        _, r = self._run_script({
            "AIRULESET_TMUX_CUTOVER_NEWEST": str(newest),
            "AIRULESET_TMUX_CUTOVER_TARGET": str(target),
        })
        self.assertEqual(r.returncode, 0, r.stderr)
        # untouched -- still pointing at the packaged binary, never at
        # something that doesn't exist.
        self.assertEqual(os.readlink(target), str(packaged))

    def test_present_but_non_executable_newest_leaves_target_untouched(self):
        # A truncated/interrupted copy of tmux-3.7b, or one that landed with
        # the wrong permissions, must never become the boot-time target
        # (adversarial-review finding, #242).
        d = self._tmp()
        newest = self._fake_tmux(Path(d) / "tmux-3.7b", executable=False)
        target = Path(d) / "tmux"
        packaged = Path(d) / "tmux-packaged"
        packaged.write_text("old packaged binary")
        target.symlink_to(packaged)
        _, r = self._run_script({
            "AIRULESET_TMUX_CUTOVER_NEWEST": str(newest),
            "AIRULESET_TMUX_CUTOVER_TARGET": str(target),
        })
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(target), str(packaged))

    def test_target_created_pointing_at_newest_when_missing_entirely(self):
        d = self._tmp()
        newest = self._fake_tmux(Path(d) / "tmux-3.7b")
        target = Path(d) / "tmux"
        _, r = self._run_script({
            "AIRULESET_TMUX_CUTOVER_NEWEST": str(newest),
            "AIRULESET_TMUX_CUTOVER_TARGET": str(target),
        })
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(target.is_symlink())
        self.assertEqual(os.readlink(target), str(newest))

    def test_wrong_target_gets_relinked_to_newest(self):
        d = self._tmp()
        newest = self._fake_tmux(Path(d) / "tmux-3.7b")
        packaged = Path(d) / "tmux-packaged"
        packaged.write_text("old packaged binary")
        target = Path(d) / "tmux"
        target.symlink_to(packaged)
        _, r = self._run_script({
            "AIRULESET_TMUX_CUTOVER_NEWEST": str(newest),
            "AIRULESET_TMUX_CUTOVER_TARGET": str(target),
        })
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(target), str(newest))

    def test_regular_file_at_target_gets_replaced_by_the_symlink(self):
        # TARGET as a plain regular file (not missing, not a symlink) is
        # the shape a freshly-`apt-get install`ed packaged tmux could
        # actually take -- ln -sfn must still cut it over cleanly.
        d = self._tmp()
        newest = self._fake_tmux(Path(d) / "tmux-3.7b")
        target = Path(d) / "tmux"
        target.write_text("packaged tmux binary, not a symlink")
        _, r = self._run_script({
            "AIRULESET_TMUX_CUTOVER_NEWEST": str(newest),
            "AIRULESET_TMUX_CUTOVER_TARGET": str(target),
        })
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(target.is_symlink())
        self.assertEqual(os.readlink(target), str(newest))

    def test_already_correct_target_is_a_true_noop(self):
        # Read-only sandbox dir: the CORRECT script never writes when
        # already correct, so it exits 0 regardless. Any mutant that drops
        # the comparison and always `ln -sfn`s would hit a real write
        # failure against the read-only dir and fail this test -- a plain
        # writable sandbox cannot tell "skipped" from "re-wrote the same
        # value" (adversarial-review finding, #242).
        d = self._tmp()
        newest = self._fake_tmux(Path(d) / "tmux-3.7b")
        target = Path(d) / "tmux"
        target.symlink_to(newest)
        os.chmod(d, 0o555)
        self.addCleanup(os.chmod, d, 0o755)
        _, r = self._run_script({
            "AIRULESET_TMUX_CUTOVER_NEWEST": str(newest),
            "AIRULESET_TMUX_CUTOVER_TARGET": str(target),
        })
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(target), str(newest))

    def test_never_touches_a_path_outside_target(self):
        d = self._tmp()
        newest = self._fake_tmux(Path(d) / "tmux-3.7b")
        target = Path(d) / "tmux"
        sentinel = Path(d) / "unrelated-file"
        sentinel.write_text("do not touch")
        before = sentinel.read_text()
        _, r = self._run_script({
            "AIRULESET_TMUX_CUTOVER_NEWEST": str(newest),
            "AIRULESET_TMUX_CUTOVER_TARGET": str(target),
        })
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sentinel.read_text(), before)


class TestTmuxCutoverUnitTemplate(TestCase):
    """The systemd unit template (settings/tmux-cutover.service.template)
    must be a boot-time oneshot ordered before any login/ssh session can
    exist, and must never be configured to auto-start."""

    def setUp(self):
        self.text = airuleset.TMUX_CUTOVER_SERVICE_TEMPLATE.read_text()

    def test_is_a_oneshot_that_remains_after_exit(self):
        self.assertIn("Type=oneshot", self.text)
        self.assertIn("RemainAfterExit=yes", self.text)

    def test_execstart_points_at_the_managed_script(self):
        self.assertIn(f"ExecStart={airuleset.TMUX_CUTOVER_SCRIPT_DEST}", self.text)

    def test_ordered_before_sysinit_and_ssh(self):
        self.assertIn("DefaultDependencies=no", self.text)
        self.assertIn("Before=sysinit.target ssh.service ssh.socket", self.text)

    def test_enabled_via_sysinit_target_never_multi_user(self):
        self.assertIn("WantedBy=sysinit.target", self.text)


def _assert_never_starts(testcase, calls):
    """The load-bearing safety invariant across every code path of both
    provisioning functions: starting the unit (by ANY of `systemctl start`,
    `enable --now`, or `restart`) could flip the symlink under a
    possibly-live tmux server, so none of these three may EVER appear as an
    argv element in any call. Checking `"start" in argv` alone (list
    membership) misses `--now` and a bare `restart` element -- caught by
    adversarial review, #242."""
    for c in calls:
        testcase.assertNotIn("start", c)
        testcase.assertNotIn("--now", c)
        testcase.assertNotIn("restart", c)


class TestSetupTmuxCutoverProvisioning(TestCase):
    """setup_tmux_cutover_provisioning: local, non-interactive (sudo -n)
    install+enable -- NEVER a `systemctl start`, on any code path, success
    or failure, because starting it could flip a symlink under a
    possibly-live tmux server."""

    def test_no_sudo_skips_with_expected_reason_and_writes_nothing(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return _FakeCP(returncode=1, stderr="sudo: a password is required")

        ok, reason = airuleset.setup_tmux_cutover_provisioning(run=run)
        self.assertFalse(ok)
        self.assertIn("no NOPASSWD sudo", reason)
        # only the probe was attempted -- no write, no systemctl call
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ["sudo", "-n", "true"])

    def test_happy_path_installs_enables_and_never_starts(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return _FakeCP(returncode=0)

        ok, reason = airuleset.setup_tmux_cutover_provisioning(run=run)
        self.assertTrue(ok)
        self.assertIsNone(reason)
        joined = [" ".join(c) for c in calls]
        self.assertTrue(any("tee /usr/local/bin/airuleset-tmux-cutover.sh" in j for j in joined))
        self.assertTrue(any("tee /etc/systemd/system/airuleset-tmux-cutover.service" in j
                             for j in joined))
        self.assertTrue(any("daemon-reload" in j for j in joined))
        self.assertTrue(any("enable" in j and "airuleset-tmux-cutover.service" in j
                             for j in joined))
        # the load-bearing safety invariant: never a `systemctl start`
        # (nor `enable --now`, nor `restart`).
        _assert_never_starts(self, calls)

    def test_write_failure_stops_before_systemctl_and_never_starts(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if argv[:3] == ["sudo", "-n", "true"]:
                return _FakeCP(returncode=0)
            if "tee" in argv:
                return _FakeCP(returncode=1, stderr="Permission denied")
            return _FakeCP(returncode=0)

        ok, reason = airuleset.setup_tmux_cutover_provisioning(run=run)
        self.assertFalse(ok)
        self.assertIn("write", reason)
        self.assertFalse(any("daemon-reload" in " ".join(c) for c in calls))
        _assert_never_starts(self, calls)

    def test_daemon_reload_failure_never_reaches_enable_or_start(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "daemon-reload" in argv:
                return _FakeCP(returncode=1, stderr="reload failed")
            return _FakeCP(returncode=0)

        ok, reason = airuleset.setup_tmux_cutover_provisioning(run=run)
        self.assertFalse(ok)
        self.assertIn("daemon-reload", reason)
        self.assertFalse(any("enable" in c for c in calls))
        _assert_never_starts(self, calls)

    def test_enable_failure_reported_and_never_starts(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "enable" in argv:
                return _FakeCP(returncode=1, stderr="enable failed")
            return _FakeCP(returncode=0)

        ok, reason = airuleset.setup_tmux_cutover_provisioning(run=run)
        self.assertFalse(ok)
        self.assertIn("enable", reason)
        _assert_never_starts(self, calls)


class TestSetupTmuxCutoverSubdevViaGatekeeper(TestCase):
    """setup_tmux_cutover_subdev_via_gatekeeper: a true no-op on every box
    without the subdev_admin identity, and on gatekeeper itself performs the
    identical install over the root@subdev hop -- again, never starting the
    unit."""

    def test_missing_identity_is_a_true_noop_no_calls_at_all(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return _FakeCP(returncode=0)

        missing = Path(tempfile.mkdtemp()) / "no-such-identity"
        ok, reason = airuleset.setup_tmux_cutover_subdev_via_gatekeeper(
            run=run, identity_path=missing)
        self.assertFalse(ok)
        self.assertIn("not the gatekeeper box", reason)
        self.assertEqual(calls, [])

    def test_present_identity_installs_over_the_sanctioned_ssh_shape(self):
        d = tempfile.mkdtemp()
        identity = Path(d) / "subdev_admin"
        identity.write_text("fake key")
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return _FakeCP(returncode=0)

        ok, reason = airuleset.setup_tmux_cutover_subdev_via_gatekeeper(
            run=run, identity_path=identity)
        self.assertTrue(ok)
        self.assertIsNone(reason)
        expected_prefix = ["ssh", "-i", str(identity),
                            "-o", "StrictHostKeyChecking=no", "root@subdev"]
        for c in calls:
            self.assertEqual(c[:len(expected_prefix)], expected_prefix)
        joined = [" ".join(c) for c in calls]
        self.assertTrue(any("airuleset-tmux-cutover.sh" in j for j in joined))
        self.assertTrue(any("airuleset-tmux-cutover.service" in j for j in joined))
        self.assertTrue(any("daemon-reload" in j for j in joined))
        self.assertTrue(any("enable airuleset-tmux-cutover.service" in j for j in joined))
        _assert_never_starts(self, calls)

    def test_remote_write_failure_stops_before_systemctl(self):
        d = tempfile.mkdtemp()
        identity = Path(d) / "subdev_admin"
        identity.write_text("fake key")
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            joined = " ".join(argv)
            if "tee" in joined:
                return _FakeCP(returncode=1, stderr="remote write failed")
            return _FakeCP(returncode=0)

        ok, reason = airuleset.setup_tmux_cutover_subdev_via_gatekeeper(
            run=run, identity_path=identity)
        self.assertFalse(ok)
        self.assertIn("subdev failed", reason)
        self.assertFalse(any("daemon-reload" in " ".join(c) for c in calls))


class TestTmuxCutoverValidation(TestCase):
    """_validate_tmux_cutover() must pass clean against this repo's own
    shipped template + script (the real files `cmd_validate` checks)."""

    def test_real_repo_files_validate_clean(self):
        self.assertEqual(airuleset._validate_tmux_cutover(), [])


class TestClaudeLauncherContinueOrNew(TestCase):
    """The managed `claude` launcher must CONTINUE (-c) only when the cwd has a
    prior conversation, and start a FRESH session otherwise — unconditional -c
    died with "No conversation found to continue" in every new directory
    (david@gk, 2026-07-09), forcing users to know about claude-new."""

    def _run_launcher(self, home, cwd, fn="claude", stub_body=None):
        bashrc = Path(home) / ".bashrc"
        script = Path(home) / ".claude" / "airuleset-claude-launch.sh"
        history_script = Path(home) / ".claude" / "airuleset-claude-history.py"
        popup_script = Path(home) / ".claude" / "airuleset-claude-history-popup.sh"
        airuleset.apply_ultracode_launcher(bashrc, script, history_script, popup_script)
        stub_dir = Path(home) / "bin"
        stub_dir.mkdir(exist_ok=True)
        stub = stub_dir / "claude"
        stub.write_text(stub_body or '#!/bin/bash\necho "ARGS:$*"\n')
        stub.chmod(0o755)
        # Hermetic regardless of the AMBIENT environment this test process
        # itself runs under (#253 review finding): CLAUDE_CODE_NO_FLICKER
        # popped so a test asserting "the default mode never sets it" can
        # never spuriously pass/fail on account of an already-exported value
        # this subprocess would otherwise inherit.
        env = {**os.environ, "HOME": str(home),
               "PATH": f"{stub_dir}:{os.environ['PATH']}"}
        env.pop("CLAUDE_CODE_NO_FLICKER", None)
        r = subprocess.run(
            ["bash", "-c", f"source {bashrc}; cd '{cwd}'; {fn}"],
            capture_output=True, text=True, env=env)
        return r.stdout

    @staticmethod
    def _proj_dir(home, cwd):
        # Claude Code encodes cwd -> projects dirname: / . _ all become -
        enc = str(cwd).replace("/", "-").replace(".", "-").replace("_", "-")
        d = Path(home) / ".claude" / "projects" / enc
        d.mkdir(parents=True)
        return d

    def test_fresh_dir_starts_new_session_without_dash_c(self):
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        out = self._run_launcher(home, cwd)
        self.assertIn("ARGS:", out)                    # stub actually ran
        self.assertIn("--dangerously-skip-permissions", out)
        self.assertNotIn(" -c", out)

    def test_dir_with_conversation_continues_with_dash_c(self):
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        (self._proj_dir(home, cwd) / "abc.jsonl").write_text("{}")
        out = self._run_launcher(home, cwd)
        self.assertIn(" -c", out)

    def test_memory_only_project_dir_still_starts_fresh(self):
        # projects/<enc>/ can exist holding only memory/ — no transcript => new
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        (self._proj_dir(home, cwd) / "memory").mkdir()
        out = self._run_launcher(home, cwd)
        self.assertNotIn(" -c", out)

    def test_underscore_and_dot_dirs_encode_to_dashes(self):
        home = tempfile.mkdtemp()
        cwd = Path(home) / "web_app.ai"
        cwd.mkdir()
        (self._proj_dir(home, cwd) / "s.jsonl").write_text("{}")
        out = self._run_launcher(home, cwd)
        self.assertIn(" -c", out)

    def test_resumed_session_gets_model_flag_explicitly(self):
        # THE BUG: a resumed (-c) session inherited its OLD transcript's model
        # rather than the managed default — nothing on the launch command line
        # forced it. Prove --model is on the ARGS bash actually executes for
        # the CONTINUE branch (a prior conversation exists for this cwd).
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        (self._proj_dir(home, cwd) / "abc.jsonl").write_text("{}")
        out = self._run_launcher(home, cwd)
        self.assertIn(" -c", out)
        self.assertIn("--model %s" % airuleset.MANAGED_MODEL, out)

    def test_fresh_session_also_gets_model_flag(self):
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        out = self._run_launcher(home, cwd)
        self.assertNotIn(" -c", out)
        self.assertIn("--model %s" % airuleset.MANAGED_MODEL, out)

    def test_default_mode_carries_ultracode_flag_continue_and_fresh(self):
        # 2026-08-13 standing-ultracode directive: the DEFAULT launcher (the
        # bare `claude` function) carries the ultracode flag on BOTH its
        # branches — a resumed (-c) session and a fresh one.
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        out = self._run_launcher(home, cwd)                      # fresh
        self.assertIn('--settings {"ultracode":true}', out)
        (self._proj_dir(home, cwd) / "abc.jsonl").write_text("{}")
        out = self._run_launcher(home, cwd)                      # continue
        self.assertIn(" -c", out)
        self.assertIn('--settings {"ultracode":true}', out)

    def test_claude_new_is_skip_perms_fresh_and_carries_ultracode(self):
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        (self._proj_dir(home, cwd) / "abc.jsonl").write_text("{}")  # a prior convo exists
        out = self._run_launcher(home, cwd, fn="claude-new")
        self.assertIn("--dangerously-skip-permissions", out)
        self.assertNotIn(" -c", out)                  # always fresh, even with a prior convo
        # 2026-08-13: ultracode is the standing default in every mode but plain
        self.assertIn('--settings {"ultracode":true}', out)

    def test_claude_ultracode_is_an_alias_of_the_default_mode(self):
        # (was ...the_deliberate_opt_in_escape_hatch — renamed for #445: the
        # mode now matches the default's behavior and is kept as an alias)
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        (self._proj_dir(home, cwd) / "abc.jsonl").write_text("{}")
        out = self._run_launcher(home, cwd, fn="claude-ultracode")
        self.assertIn(" -c", out)
        self.assertIn('--settings {"ultracode":true}', out)
        self.assertIn("--model %s" % airuleset.MANAGED_MODEL, out)

    def test_claude_plain_carries_no_flags_at_all(self):
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        out = self._run_launcher(home, cwd, fn="claude-plain")
        self.assertEqual(out.strip(), "ARGS:")

    def _run_launcher_with_env_probe(self, home, cwd, fn):
        # Like _run_launcher, but the stub also echoes CLAUDE_CODE_NO_FLICKER
        # so a test can assert what the launch script actually EXPORTED.
        return self._run_launcher(
            home, cwd, fn=fn,
            stub_body='#!/bin/bash\n'
                      'echo "ARGS:$* NOFLICKER:${CLAUDE_CODE_NO_FLICKER:-}"\n')

    def test_claude_fullscreen_sets_no_flicker_and_preserves_continue_or_new(self):
        # #253: proven upstream renderer defect (anthropics/claude-code#84247 /
        # #46834) stacks duplicate/interleaved frames into tmux's native
        # scrollback on every SIGWINCH/relayout. CLAUDE_CODE_NO_FLICKER=1
        # switches Claude Code to the alternate-screen TUI, which never
        # writes into the terminal's native scrollback at all -- sidestepping
        # the defect class entirely (confirmed by upstream reporters on
        # #46834). This is otherwise identical to the `default` mode
        # (continue-or-new, skip-perms, managed model) -- only the env var
        # differs.
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        (self._proj_dir(home, cwd) / "abc.jsonl").write_text("{}")
        out = self._run_launcher_with_env_probe(home, cwd, fn="claude-fullscreen")
        self.assertIn("NOFLICKER:1", out)
        self.assertIn(" -c", out)
        self.assertIn("--model %s" % airuleset.MANAGED_MODEL, out)
        self.assertIn("--dangerously-skip-permissions", out)
        # 2026-08-13: ultracode is the standing default in every mode but plain
        self.assertIn('--settings {"ultracode":true}', out)

    def test_claude_fullscreen_fresh_dir_starts_new_without_dash_c(self):
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        out = self._run_launcher_with_env_probe(home, cwd, fn="claude-fullscreen")
        self.assertIn("NOFLICKER:1", out)
        self.assertNotIn(" -c", out)

    def test_default_mode_never_sets_no_flicker(self):
        # The opt-in escape hatch must never leak into the default launch --
        # forcing fullscreen mode on every session is a UX tradeoff the user
        # did not ask for.
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        out = self._run_launcher_with_env_probe(home, cwd, fn="claude")
        self.assertNotIn("NOFLICKER:1", out, out)

    def test_frozen_shell_gets_new_launcher_behavior_without_resourcing(self):
        # THE BUG (#77): a bashrc FUNCTION is parsed once at shell startup and
        # then frozen in that shell's memory forever -- a `push` rewriting
        # .bashrc had ZERO effect on an already-running panel shell (measured
        # live: two sessions launched HOURS/DAYS after #53 landed still carried
        # the pre-#53 behavior). The fix: .bashrc holds only a thin wrapper
        # that execs a SCRIPT, read fresh on every call. Prove it: source
        # .bashrc ONCE in a persistent shell, observe behavior A, then mutate
        # ONLY the script file (simulating a `push`) WITHOUT touching that
        # shell again -- the very next `claude` call in the SAME shell must
        # already show the new behavior.
        home = tempfile.mkdtemp()
        cwd = Path(home) / "proj"
        cwd.mkdir()
        bashrc = Path(home) / ".bashrc"
        script = Path(home) / ".claude" / "airuleset-claude-launch.sh"
        history_script = Path(home) / ".claude" / "airuleset-claude-history.py"
        popup_script = Path(home) / ".claude" / "airuleset-claude-history-popup.sh"
        airuleset.apply_ultracode_launcher(bashrc, script, history_script, popup_script)
        stub_dir = Path(home) / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "claude"
        stub.write_text('#!/bin/bash\necho "ARGS:$*"\n')
        stub.chmod(0o755)
        env = {**os.environ, "HOME": str(home),
               "PATH": f"{stub_dir}:{os.environ['PATH']}"}

        proc = subprocess.Popen(
            ["bash"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, env=env, cwd=str(cwd))
        try:
            proc.stdin.write(f"source {bashrc}\n")
            proc.stdin.flush()

            def _run(cmd, marker):
                proc.stdin.write(f"{cmd}\necho {marker}\n")
                proc.stdin.flush()
                lines = []
                while True:
                    line = proc.stdout.readline()
                    if not line or line.strip() == marker:
                        break
                    lines.append(line)
                return "".join(lines)

            before = _run("claude", "__M1__")
            self.assertNotIn("--v2marker", before)

            # simulate a `push`: rewrite the SCRIPT only. The already-sourced
            # shell above is NEVER touched again.
            mutated = script.read_text().replace("exec claude ", "exec claude --v2marker ")
            self.assertNotEqual(mutated, script.read_text())
            script.write_text(mutated)

            after = _run("claude", "__M2__")
            self.assertIn("--v2marker", after, after)
        finally:
            proc.stdin.write("exit\n")
            proc.stdin.flush()
            proc.wait(timeout=10)


class TestClaudeHistoryScript(TestCase):
    """#267: `claude-history` -- a readable, un-corrupted view of a Claude
    Code session, read straight from its own transcript JSONL (the source
    of truth). Measured live (scripts/measure_scrollback_holes.py, results
    pinned to the ticket): CLAUDE_CODE_NO_FLICKER=1 does NOT fix tmux
    scrollback holes -- it makes native scrollback almost entirely EMPTY
    (78.5-87.33% of a generated response missing even with zero relayout
    stress), categorically worse than default mode's real-but-small
    corruption (0-6%, only after an actual relayout event). This is the
    honest companion instead: it never touches the terminal renderer at
    all, so it is structurally immune to the class of defect (#253:
    anthropics/claude-code#84247/#46834) this whole ticket is about.

    Every test here runs the ACTUAL rendered script content
    (render_claude_history_script()) as a real subprocess against a
    synthetic transcript directory -- never a reimplementation of its
    parsing logic -- so a regression in the shipped script is what fails,
    not a regression in a parallel test-only copy."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.script = self.home / "claude-history.py"
        self.script.write_text(airuleset.render_claude_history_script())
        os.chmod(self.script, 0o755)
        self.projects_dir = self.home / ".claude" / "projects"

    def _write_transcript(self, cwd, lines, sid="s1"):
        enc = airuleset.encode_project_dir(str(cwd))
        d = self.projects_dir / enc
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.jsonl"
        p.write_text("\n".join(json.dumps(ln) for ln in lines) + "\n")
        return p

    def _run(self, *args, env=None):
        # `Path.home()` inside the script must resolve to the ISOLATED
        # self.home, not the real box's $HOME -- --transcript bypasses
        # this (it's given an absolute path directly), but --cwd/--pane/
        # --list all read ~/.claude/projects/... under Path.home().
        run_env = {**os.environ, "HOME": str(self.home)}
        if env:
            run_env.update(env)
        r = subprocess.run(
            [sys.executable, str(self.script), *args],
            capture_output=True, text=True, timeout=15, env=run_env)
        return r

    @staticmethod
    def _user(text, uuid=None, parent_uuid=None):
        rec = {"type": "user", "message": {"role": "user", "content": text}}
        if uuid is not None:
            rec["uuid"] = uuid
        if parent_uuid is not None:
            rec["parentUuid"] = parent_uuid
        return rec

    @staticmethod
    def _assistant(*blocks, uuid=None, parent_uuid=None):
        rec = {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}
        if uuid is not None:
            rec["uuid"] = uuid
        if parent_uuid is not None:
            rec["parentUuid"] = parent_uuid
        return rec

    @staticmethod
    def _compact_boundary(pre=None, post=None, uuid=None, parent_uuid=None):
        rec = {"type": "system", "subtype": "compact_boundary"}
        if pre is not None or post is not None:
            rec["compactMetadata"] = {"preTokens": pre, "postTokens": post}
        if uuid is not None:
            rec["uuid"] = uuid
        if parent_uuid is not None:
            rec["parentUuid"] = parent_uuid
        return rec

    @staticmethod
    def _text_block(t):
        return {"type": "text", "text": t}

    @staticmethod
    def _tool_block(name, **inp):
        return {"type": "tool_use", "name": name, "input": inp}

    def test_reads_a_real_transcript_and_shows_user_and_assistant_turns(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [
            self._user("what is 2+2?"),
            self._assistant(self._text_block("2+2 is 4.")),
        ])
        r = self._run("--transcript", str(self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "s1.jsonl"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("===== USER =====", r.stdout)
        self.assertIn("what is 2+2?", r.stdout)
        self.assertIn("===== CLAUDE =====", r.stdout)
        self.assertIn("2+2 is 4.", r.stdout)

    def test_resolves_the_newest_transcript_for_a_cwd_by_default(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("old session")], sid="old")
        old_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "old.jsonl"
        os.utime(old_path, (1000, 1000))
        self._write_transcript(cwd, [self._user("new session")], sid="new")
        new_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "new.jsonl"
        os.utime(new_path, (2000, 2000))
        r = self._run("--cwd", str(cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("new session", r.stdout)
        self.assertNotIn("old session", r.stdout)

    def test_cwd_with_underscore_and_dot_resolves_correctly(self):
        # ADVERSARIAL-REVIEW FINDING (#267, MINOR -- F2): the OTHER tests in
        # this class use a plain "proj" cwd, so a mutant that broke the
        # embedded script's OWN copy of encode_project_dir (e.g. dropping
        # the "_" -> "-" substitution) only got caught in 9/12 mutation
        # runs -- purely by luck of tempfile's random suffix sometimes
        # containing an underscore. A cwd carrying BOTH special characters
        # `encode_project_dir` must transform makes this deterministic.
        cwd = self.home / "my_proj.v2"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("underscore and dot"),
                                      self._assistant(self._text_block("found it"))])
        r = self._run("--cwd", str(cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("found it", r.stdout)

    def test_encode_project_dir_copies_stay_in_sync(self):
        # ADVERSARIAL-REVIEW FINDING (#267, MINOR -- F2): airuleset.py's own
        # top-level encode_project_dir and the IDENTICAL copy embedded
        # inline in CLAUDE_HISTORY_SCRIPT_CONTENT (the deployed script
        # cannot import airuleset.py itself) must never silently drift
        # apart. Load the embedded copy as its own module (no dataclasses
        # involved, so no sys.modules pre-registration needed) and compare
        # against a battery of real Claude Code cwd shapes.
        import importlib.util
        spec = importlib.util.spec_from_file_location("_claude_history_probe", self.script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for cwd in ("/home/newlevel/devel/airuleset",
                    "/home/newlevel/devel/tomas_pardubsky/cold_mailing",
                    "/tmp/web_app.ai", "/a/b.c_d"):
            self.assertEqual(mod.encode_project_dir(cwd),
                              airuleset.encode_project_dir(cwd), cwd)

    def test_merges_multiple_content_blocks_of_the_same_turn_into_one(self):
        # A real Claude Code assistant response is written as SEVERAL jsonl
        # lines (one per content block, #131) -- but here it's ONE
        # assistant record with several blocks in `content`; the important
        # behavioral claim is that a `tool_use` block renders as an
        # activity line ALONGSIDE the surrounding text, in one turn.
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [
            self._user("list the files"),
            self._assistant(
                self._text_block("Let me check."),
                self._tool_block("Bash", command="ls -la"),
                self._text_block("Done."),
            ),
        ])
        r = self._run("--cwd", str(cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        # exactly one CLAUDE turn header, not one per content block
        self.assertEqual(r.stdout.count("===== CLAUDE ====="), 1)
        self.assertIn("Let me check.", r.stdout)
        self.assertIn("Done.", r.stdout)
        self.assertIn("Bash: ls -la", r.stdout)

    def test_skips_tool_result_entries_and_system_noise(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [
            self._user("do something"),
            {"type": "user", "message": {"role": "user",
                                          "content": [{"type": "tool_result", "content": "output"}]}},
            {"type": "system", "subtype": "compact_boundary"},
            self._assistant(self._text_block("done")),
        ])
        r = self._run("--cwd", str(cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("===== USER ====="), 1)  # tool_result not shown as a user turn
        self.assertNotIn("compact_boundary", r.stdout)

    def test_last_truncates_to_the_most_recent_n_turns(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        records = []
        for i in range(5):
            records.append(self._user(f"question {i}"))
            records.append(self._assistant(self._text_block(f"answer {i}")))
        self._write_transcript(cwd, records)
        r = self._run("--cwd", str(cwd), "--last", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("question 4", r.stdout)
        self.assertIn("answer 4", r.stdout)
        self.assertNotIn("question 0", r.stdout)
        self.assertIn("10 turn(s) total", r.stdout)

    def test_full_shows_every_turn_regardless_of_last(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        records = []
        for i in range(5):
            records.append(self._user(f"q{i}"))
            records.append(self._assistant(self._text_block(f"a{i}")))
        self._write_transcript(cwd, records)
        r = self._run("--cwd", str(cwd), "--full")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("q0", r.stdout)
        self.assertIn("q4", r.stdout)

    def test_width_wraps_long_body_text_to_the_given_column_count(self):
        # #376 real-user report against the popup at 90%/narrow width:
        # long lines "scroll right instead of wrapping" -- unusable.
        # `--width` word-wraps body text so no rendered LINE exceeds it.
        cwd = self.home / "proj"
        cwd.mkdir()
        long_text = " ".join(f"word{i}" for i in range(40))  # far > 20 cols
        self._write_transcript(cwd, [
            self._user("q"),
            self._assistant(self._text_block(long_text)),
        ])
        r = self._run("--cwd", str(cwd), "--full", "--width", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("word0", r.stdout)
        self.assertIn("word39", r.stdout)
        # scope the width check to the rendered TURN content -- the "# ..."
        # header/count lines are metadata, never subject to body wrapping.
        body_start = r.stdout.index("===== CLAUDE =====")
        for line in r.stdout[body_start:].splitlines():
            self.assertLessEqual(len(line), 20, line)
        # never split IN THE MIDDLE of one word -- word39 stays intact.
        self.assertIn("word39", r.stdout)

    def test_width_zero_or_omitted_never_wraps(self):
        # 0/omitted must be byte-for-byte the pre-#376 unwrapped behavior --
        # regression guard for every OTHER test in this class that never
        # passes --width at all.
        cwd = self.home / "proj"
        cwd.mkdir()
        long_text = " ".join(f"word{i}" for i in range(40))
        self._write_transcript(cwd, [
            self._user("q"),
            self._assistant(self._text_block(long_text)),
        ])
        r = self._run("--cwd", str(cwd), "--full")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(long_text, r.stdout)

    # -- #376: readable compaction-boundary marker + whole-file completeness
    # (never silently drop pre-compact / orphaned-branch content) + chain
    # every session file for the project, not just the newest -----------

    def test_full_shows_a_readable_marker_for_a_compaction_boundary(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [
            self._user("before compaction"),
            self._assistant(self._text_block("ack")),
            self._compact_boundary(pre=123456, post=789),
            self._user("after compaction"),
            self._assistant(self._text_block("still here")),
        ])
        r = self._run("--cwd", str(cwd), "--full")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("COMPACTED", r.stdout)
        self.assertIn("123456", r.stdout)
        self.assertIn("789", r.stdout)
        self.assertIn("before compaction", r.stdout)
        self.assertIn("after compaction", r.stdout)

    def test_full_renders_the_full_history_across_compaction_including_orphaned_pre_compact_branches(self):
        # A real Claude Code transcript is a UUID/parentUuid TREE, not a
        # flat list -- an interrupted/retried turn leaves an ORPHANED
        # sibling branch (never chosen as the live leaf) physically present
        # in the file. The acceptance is COMPLETENESS (never silently drop
        # data), not branch selection -- both the abandoned branch and the
        # real continuation must render, on both sides of a compaction.
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [
            self._user("kto si?", uuid="u1"),
            self._assistant(self._text_block("Som stream."), uuid="u2", parent_uuid="u1"),
            # orphaned branch: an alternate, abandoned child of u1
            self._user("ORPHANED aky si model?", uuid="u3-orphan", parent_uuid="u1"),
            self._assistant(self._text_block("ORPHANED-REPLY"), uuid="u4-orphan", parent_uuid="u3-orphan"),
            # the real continuation instead comes from u2
            self._user("pokracujme", uuid="u5", parent_uuid="u2"),
            self._assistant(self._text_block("ok"), uuid="u6", parent_uuid="u5"),
            self._compact_boundary(pre=100000, post=500, uuid="u7", parent_uuid="u6"),
            self._user("Continue the conversation from where it left off.", uuid="u8"),
            self._assistant(self._text_block("Pokracujem po kompaktovani."), uuid="u9", parent_uuid="u8"),
        ])
        r = self._run("--cwd", str(cwd), "--full")
        self.assertEqual(r.returncode, 0, r.stderr)
        for expected in ("kto si?", "Som stream.", "ORPHANED aky si model?",
                          "ORPHANED-REPLY", "pokracujme", "ok", "COMPACTED",
                          "Continue the conversation from where it left off.",
                          "Pokracujem po kompaktovani."):
            self.assertIn(expected, r.stdout, expected)

    def test_full_chains_all_session_files_for_the_project_not_just_the_newest(self):
        # A `claude-new`-mode fresh session (or any other reason a second
        # session id/file exists for one project) must never silently drop
        # the OLDER file's own content once a newer one exists.
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("older session content"),
                                      self._assistant(self._text_block("older reply"))], sid="old")
        old_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "old.jsonl"
        os.utime(old_path, (1000, 1000))
        self._write_transcript(cwd, [self._user("newer session content"),
                                      self._assistant(self._text_block("newer reply"))], sid="new")
        new_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "new.jsonl"
        os.utime(new_path, (2000, 2000))
        r = self._run("--cwd", str(cwd), "--full")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("older session content", r.stdout)
        self.assertIn("older reply", r.stdout)
        self.assertIn("newer session content", r.stdout)
        self.assertIn("newer reply", r.stdout)
        # chronological: the older file's content appears BEFORE the newer's
        self.assertLess(r.stdout.index("older session content"),
                         r.stdout.index("newer session content"))

    def test_full_dedupes_a_uuid_appearing_in_more_than_one_chained_file(self):
        # `only in old`/`only in new` are the DISCRIMINATOR: this test must
        # fail two DIFFERENT ways depending on which half of the fix is
        # missing -- chaining not implemented at all ("only in old"
        # absent, since the old file is never even read) vs. chaining
        # implemented WITHOUT dedup ("shared prompt" appears twice). A
        # naive version of this test asserting only the dedup count would
        # pass "green" even with zero chaining (the old file's content
        # simply never read at all) -- this shape catches that trap.
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("shared prompt", uuid="dup1"),
                                      self._user("only in old", uuid="only-old")], sid="old")
        old_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "old.jsonl"
        os.utime(old_path, (1000, 1000))
        self._write_transcript(cwd, [self._user("shared prompt", uuid="dup1"),
                                      self._user("only in new", uuid="only-new")], sid="new")
        new_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "new.jsonl"
        os.utime(new_path, (2000, 2000))
        r = self._run("--cwd", str(cwd), "--full")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("only in old", r.stdout)
        self.assertIn("only in new", r.stdout)
        self.assertEqual(r.stdout.count("shared prompt"), 1)

    def test_default_last_mode_still_uses_only_the_newest_file_when_multiple_exist(self):
        # Scoping decision (#376 design comment): chaining is `--full`-only.
        # `--last` (the quick-glance default) keeps its EXISTING single-
        # newest-file behavior unchanged -- locks
        # test_resolves_the_newest_transcript_for_a_cwd_by_default's own
        # claim under the presence of a genuinely older sibling file too.
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("old session")], sid="old")
        old_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "old.jsonl"
        os.utime(old_path, (1000, 1000))
        self._write_transcript(cwd, [self._user("new session")], sid="new")
        new_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "new.jsonl"
        os.utime(new_path, (2000, 2000))
        r = self._run("--cwd", str(cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("new session", r.stdout)
        self.assertNotIn("old session", r.stdout)

    def test_list_prints_every_transcript_for_the_cwd_without_rendering_content(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("secret prompt")], sid="s1")
        r = self._run("--cwd", str(cwd), "--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("s1.jsonl", r.stdout)
        self.assertNotIn("secret prompt", r.stdout)

    def test_no_transcript_for_cwd_fails_loud_not_silent(self):
        cwd = self.home / "empty-proj"
        cwd.mkdir()
        r = self._run("--cwd", str(cwd))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no Claude Code session transcript found", r.stderr)

    def test_pane_flag_resolves_cwd_via_tmux_display_message(self):
        # A fake `tmux` on PATH proves the script asks display-message for
        # the pane's cwd -- never a keystroke, never touching a real pane.
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("hi"), self._assistant(self._text_block("hello"))])
        stub_dir = self.home / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "tmux"
        stub.write_text(
            "#!/bin/bash\n"
            "if [ \"$1\" = display-message ]; then echo '%s'; exit 0; fi\n"
            "exit 1\n" % cwd)
        stub.chmod(0o755)
        r = self._run("--pane", "%3",
                      env={"PATH": f"{stub_dir}:{os.environ['PATH']}"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hello", r.stdout)

    def test_explicit_transcript_path_overrides_cwd_resolution(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        p = self._write_transcript(cwd, [self._user("direct read"),
                                          self._assistant(self._text_block("ok"))])
        r = self._run("--transcript", str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("direct read", r.stdout)

    # --- #294: ANSI colors + structure ------------------------------------
    # Every check below runs the ACTUAL rendered script content as a real
    # subprocess (same discipline as the rest of this class) -- a
    # reimplementation of the color logic in the test itself would prove
    # nothing about the shipped script.

    @staticmethod
    def _strip_ansi(s):
        return re.sub(r"\x1b\[[0-9;]*m", "", s)

    def _run_pty(self, *args):
        # A REAL pty slave as the child's stdout -- os.isatty() on it
        # returns True, exactly like a genuine terminal -- so this proves
        # the TTY-auto-detection half (`sys.stdout.isatty()`), which a
        # subprocess.run(capture_output=True) pipe (used by self._run)
        # structurally cannot: a pipe is never a tty.
        import pty
        run_env = {**os.environ, "HOME": str(self.home)}
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, str(self.script), *args],
            stdout=slave_fd, stderr=subprocess.DEVNULL, env=run_env)
        os.close(slave_fd)
        try:
            proc.wait(timeout=15)
            chunks = []
            while True:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode(errors="replace")
        finally:
            os.close(master_fd)

    def test_default_piped_output_has_zero_ansi_escapes(self):
        # The exact acceptance line from #294: "claude-history | cat ostáva
        # čistý text bez ANSI kódov" -- subprocess-captured stdout (used by
        # self._run) is never a tty, and no --color flag is given here, so
        # this is the auto-detected-off path.
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("hi"),
                                      self._assistant(self._text_block("hello"))])
        r = self._run("--cwd", str(cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("\x1b[", r.stdout)

    def test_color_flag_forces_ansi_even_when_piped(self):
        # The popup's own use case: claude-history's stdout is a PIPE
        # (captured into a bash variable, then piped into `less`), so TTY
        # auto-detection alone can never turn colors on for it -- --color
        # forces it regardless of what's attached to stdout.
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("hi"),
                                      self._assistant(self._text_block("hello"))])
        r = self._run("--cwd", str(cwd), "--color")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("\x1b[", r.stdout)
        # ANSI codes wrap around the existing plain text, never splice into
        # the middle of it -- every plain substring stays intact.
        self.assertIn("===== USER =====", r.stdout)
        self.assertIn("===== CLAUDE =====", r.stdout)
        self.assertIn("hi", r.stdout)
        self.assertIn("hello", r.stdout)

    def test_plain_flag_forces_off(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("hi"),
                                      self._assistant(self._text_block("hello"))])
        r = self._run("--cwd", str(cwd), "--plain")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("\x1b[", r.stdout)

    def test_color_uses_a_different_code_for_user_and_claude_headers(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("hi"),
                                      self._assistant(self._text_block("hello"))])
        r = self._run("--cwd", str(cwd), "--color")
        self.assertEqual(r.returncode, 0, r.stderr)
        user_line = next(ln for ln in r.stdout.splitlines() if "USER" in ln)
        claude_line = next(ln for ln in r.stdout.splitlines() if "CLAUDE" in ln)
        user_code = re.search(r"\x1b\[[0-9;]*m", user_line)
        claude_code = re.search(r"\x1b\[[0-9;]*m", claude_line)
        self.assertIsNotNone(user_code, user_line)
        self.assertIsNotNone(claude_code, claude_line)
        self.assertNotEqual(user_code.group(0), claude_code.group(0))

    def test_color_assigns_the_specific_established_palette_to_each_role(self):
        # #294 adversarial review (MINOR/THEORETICAL): the sibling test
        # above only proves the two header colors DIFFER, so a mutant
        # swapping which constant goes to which role (or substituting an
        # unrelated pair of colors entirely) would pass it unnoticed.
        # Pin the EXACT codes the design comment specifies: 75 for USER
        # (matches statusbar.py's existing "Issues" segment blue, reused
        # for consistency) and 108 for CLAUDE (a muted sage/olive green).
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("hi"),
                                      self._assistant(self._text_block("hello"))])
        r = self._run("--cwd", str(cwd), "--color")
        self.assertEqual(r.returncode, 0, r.stderr)
        user_line = next(ln for ln in r.stdout.splitlines() if "USER" in ln)
        claude_line = next(ln for ln in r.stdout.splitlines() if "CLAUDE" in ln)
        self.assertIn("\x1b[1;38;5;75m", user_line, user_line)
        self.assertIn("\x1b[1;38;5;108m", claude_line, claude_line)

    def test_tool_call_lines_are_dimmed_in_color_mode(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [
            self._user("list files"),
            self._assistant(self._text_block("checking"),
                             self._tool_block("Bash", command="ls -la")),
        ])
        r = self._run("--cwd", str(cwd), "--color")
        self.assertEqual(r.returncode, 0, r.stderr)
        tool_line = next(ln for ln in r.stdout.splitlines() if "Bash: ls -la" in ln)
        self.assertIn("\x1b[2m", tool_line)

    def test_timestamp_shown_dimmed_when_present_omitted_when_absent(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        enc = airuleset.encode_project_dir(str(cwd))
        proj_dir = self.projects_dir / enc
        proj_dir.mkdir(parents=True)
        lines = [
            {"type": "user", "timestamp": "2026-08-07T12:34:56.000Z",
             "message": {"role": "user", "content": "with a timestamp"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "no timestamp on me"}]}},
        ]
        (proj_dir / "s1.jsonl").write_text(
            "\n".join(json.dumps(x) for x in lines) + "\n")
        r = self._run("--cwd", str(cwd), "--color")
        self.assertEqual(r.returncode, 0, r.stderr)
        user_line = next(ln for ln in r.stdout.splitlines() if "USER" in ln)
        self.assertIn("12:34:56", user_line)
        self.assertIn("\x1b[2m", user_line)
        claude_line = next(ln for ln in r.stdout.splitlines() if "CLAUDE" in ln)
        # No timestamp on the assistant record -> nothing extra beyond the
        # (color-wrapped) header itself once ANSI is stripped back out.
        self.assertEqual(self._strip_ansi(claude_line), "===== CLAUDE =====")
        # #294 adversarial review (MINOR): a bare "HH:MM:SS" with no marker
        # reads as ambiguous local-vs-UTC time -- real transcript
        # timestamps are always UTC ("...Z" suffix), so the rendered
        # suffix carries an explicit "Z" too.
        self.assertIn("12:34:56Z", self._strip_ansi(user_line))

    def test_colors_on_by_default_on_a_real_terminal(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("hi"),
                                      self._assistant(self._text_block("hello"))])
        out = self._run_pty("--cwd", str(cwd))
        self.assertIn("\x1b[", out)

    def test_plain_flag_forces_off_even_on_a_real_terminal(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("hi"),
                                      self._assistant(self._text_block("hello"))])
        out = self._run_pty("--cwd", str(cwd), "--plain")
        self.assertNotIn("\x1b[", out)

    # -- #410: claude-history must READ a gzip-compressed `.jsonl.gz`
    # transcript exactly like a plain `.jsonl` one -- landing BEFORE any
    # compression code exists anywhere, so history-browsing of a
    # compressed file is never broken by a later commit.

    def _write_transcript_gz(self, cwd, lines, sid="s1"):
        """Same shape as _write_transcript, but writes a REAL gzip-
        compressed `<sid>.jsonl.gz` -- never a plain file with a renamed
        extension, so a test proves the actual gzip decode path."""
        import gzip
        enc = airuleset.encode_project_dir(str(cwd))
        d = self.projects_dir / enc
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.jsonl.gz"
        content = "\n".join(json.dumps(ln) for ln in lines) + "\n"
        with gzip.open(p, "wt", encoding="utf-8") as f:
            f.write(content)
        return p

    def test_reads_a_compressed_transcript_via_explicit_path(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        gz_path = self._write_transcript_gz(cwd, [
            self._user("what is 2+2?"),
            self._assistant(self._text_block("2+2 is 4.")),
        ])
        r = self._run("--transcript", str(gz_path))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("===== USER =====", r.stdout)
        self.assertIn("what is 2+2?", r.stdout)
        self.assertIn("===== CLAUDE =====", r.stdout)
        self.assertIn("2+2 is 4.", r.stdout)

    def test_cwd_resolution_finds_a_compressed_transcript_with_no_plain_sibling(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript_gz(cwd, [self._user("only a gz session exists")])
        r = self._run("--cwd", str(cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("only a gz session exists", r.stdout)

    def test_cwd_resolution_picks_the_newest_across_mixed_plain_and_gz(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript_gz(cwd, [self._user("old compressed session")], sid="old")
        old_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "old.jsonl.gz"
        os.utime(old_path, (1000, 1000))
        self._write_transcript(cwd, [self._user("new plain session")], sid="new")
        new_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "new.jsonl"
        os.utime(new_path, (2000, 2000))
        r = self._run("--cwd", str(cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("new plain session", r.stdout)
        self.assertNotIn("old compressed session", r.stdout)

    def test_list_shows_both_plain_and_compressed_transcripts(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript(cwd, [self._user("a")], sid="plain-one")
        self._write_transcript_gz(cwd, [self._user("b")], sid="gz-one")
        r = self._run("--cwd", str(cwd), "--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("plain-one.jsonl", r.stdout)
        self.assertIn("gz-one.jsonl.gz", r.stdout)

    def test_full_chains_a_compressed_older_file_with_a_plain_newer_one(self):
        cwd = self.home / "proj"
        cwd.mkdir()
        self._write_transcript_gz(cwd, [self._user("older, compressed")], sid="a")
        old_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "a.jsonl.gz"
        os.utime(old_path, (1000, 1000))
        self._write_transcript(cwd, [self._user("newer, plain")], sid="b")
        new_path = self.projects_dir / airuleset.encode_project_dir(str(cwd)) / "b.jsonl"
        os.utime(new_path, (2000, 2000))
        r = self._run("--cwd", str(cwd), "--full")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("older, compressed", r.stdout)
        self.assertIn("newer, plain", r.stdout)
        # oldest-first chaining -- the compressed turn must render BEFORE
        # the plain one, not merely be present somewhere.
        self.assertLess(r.stdout.index("older, compressed"),
                        r.stdout.index("newer, plain"))

    def test_corrupted_gz_file_fails_loudly_never_silently(self):
        # test-strictness.md: a broken dependency must FAIL, never silently
        # succeed with no output -- a `.jsonl.gz` extension whose bytes are
        # NOT a real gzip stream (e.g. a half-written/corrupted compress
        # attempt) must be reported as an error, never rendered as if it
        # were empty or valid.
        cwd = self.home / "proj"
        cwd.mkdir()
        enc = airuleset.encode_project_dir(str(cwd))
        d = self.projects_dir / enc
        d.mkdir(parents=True)
        bad = d / "broken.jsonl.gz"
        bad.write_bytes(b"this is not a real gzip stream at all")
        r = self._run("--transcript", str(bad))
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(r.stderr.strip(), "a corrupted .gz must report an error on stderr")

    def test_truncated_gz_stream_raises_eoferror_not_oserror_still_fails_loudly(self):
        # #410 review F3 (MAJOR, live-triggered): a `.jsonl.gz` with a
        # VALID header but an INCOMPLETE compressed body raises EOFError
        # from inside gzip's own decompressor -- never an OSError -- so
        # the pre-fix `except OSError` let it escape UNCAUGHT, crashing
        # the whole invocation with a raw Python traceback instead of
        # the same controlled "cannot read" stderr message every other
        # broken-transcript case produces (and, under --full, aborting
        # the render of every OTHER healthy chained file too). The
        # bogus-bytes sibling test above only ever reaches
        # BadGzipFile, which IS an OSError subtype the pre-fix code
        # already caught fine -- this needs a REAL, valid gzip stream,
        # truncated mid-body, to reach the actual gap.
        import gzip
        cwd = self.home / "proj"
        cwd.mkdir()
        enc = airuleset.encode_project_dir(str(cwd))
        d = self.projects_dir / enc
        d.mkdir(parents=True)
        real = gzip.compress(
            ("\n".join(json.dumps(self._user("line %d" % i)) for i in range(200)) + "\n")
            .encode("utf-8"))
        truncated = d / "broken.jsonl.gz"
        truncated.write_bytes(real[: len(real) // 2])
        r = self._run("--transcript", str(truncated))
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(r.stderr.strip(), "a truncated .gz must report an error on stderr")
        self.assertNotIn("Traceback", r.stderr,
                         "must be a CONTROLLED failure, never an uncaught-exception crash")


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

    # --- per-owner thread routing ----------------------------------------
    def test_notification_channel_per_owner_wins(self):
        # Each owner posts to THEIR own thread when configured (claude-zbynek /
        # claude-marek) — the @mention in a shared thread was not enough.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "shared",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread",
               "DISCORD_NOTIFICATION_CHANNEL_MAREK": "mthread"}
        self.assertEqual(self.notify.notification_channel(env=env, owner="zbynek"),
                         "zthread")
        self.assertEqual(self.notify.notification_channel(env=env, owner="marek"),
                         "mthread")

    def test_notification_channel_falls_back_to_shared(self):
        # Owner with no per-owner thread, AND unknown / empty owner → shared id.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "shared",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
        self.assertEqual(self.notify.notification_channel(env=env, owner="marek"),
                         "shared")           # no per-marek thread yet
        self.assertEqual(self.notify.notification_channel(env=env, owner="nobody"),
                         "shared")
        self.assertEqual(self.notify.notification_channel(env=env, owner=""),
                         "shared")

    def test_notification_channel_empty_when_nothing_set(self):
        self.assertEqual(self.notify.notification_channel(env={}, owner="zbynek"), "")

    # --- parallel mirror recipients (DISCORD_MIRROR_<OWNER>) ---------------
    def test_mirror_owners_parses_list_dedups_and_excludes_self(self):
        # david → also zbynek (a persona's notifications ALSO ping a real person);
        # comma/space separated, self excluded, dupes collapsed, all lowercased.
        env = {"DISCORD_MIRROR_DAVID": "zbynek, marek zbynek DAVID"}
        self.assertEqual(self.notify.mirror_owners(env=env, owner="david"),
                         ["zbynek", "marek"])

    def test_mirror_owners_empty_when_unset_or_no_owner(self):
        self.assertEqual(self.notify.mirror_owners(env={}, owner="david"), [])
        self.assertEqual(self.notify.mirror_owners(
            env={"DISCORD_MIRROR_ZBYNEK": "marek"}, owner=""), [])
        # a normal single-owner box (no mirror configured) → no fan-out
        self.assertEqual(self.notify.mirror_owners(
            env={"DISCORD_MENTION_ZBYNEK": "1"}, owner="zbynek"), [])

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
        # header is JUST the number — the technical title is dropped, not repeated
        self.assertIn("🎫 **#41**", card)
        self.assertNotIn("NDI rebind", card)
        # the Double-review line was removed (always ✅ on a clean merge = redundant)
        self.assertNotIn("Double-review", card)
        # backlog progress
        self.assertIn("hotové 3 · ostáva 5", card)
        # deploy line = the DEPLOYED VERSION (the fact the user wants); PR # removed
        self.assertIn("nasadené **v1.4.2**", card)
        self.assertNotIn("PR #", card)
        # NO stray separator right after the box emoji
        self.assertNotIn("📦 ·", card)

    def test_card_omits_double_review_line(self):
        # the Double-review line was removed at the user's request — it was always
        # ✅ on a clean merge (the only time a card fires), so pure repetition.
        for ok in (True, False):
            card = self.notify.compose_autopilot_card(
                repo="o/x", pr=1, tickets=[{"n": 1, "goal": "g", "achieved": "a"}],
                review_ok=ok, done=1, remaining=0)
            self.assertNotIn("Double-review", card)
            self.assertNotIn("NESPLNENÉ", card)
        # remaining 0 → backlog-empty flourish still present
        self.assertIn("backlog prázdny", card)

    def test_card_links_live_urls_not_pr(self):
        # 🔗 line: "where to see it live" url(s) only — the PR/diff link is NOT shown
        card = self.notify.compose_autopilot_card(
            repo="o/x", pr="https://github.com/o/x/pull/12",
            urls=["https://app.x.sk", "Money Gate=https://prod.x.sk/money-gate"],
            tickets=[{"n": 1, "goal": "g", "achieved": "a"}])
        self.assertIn("🔗", card)
        self.assertNotIn("kód (PR)", card)                        # PR link removed
        self.assertNotIn("/pull/12", card)                        # PR url not rendered
        self.assertIn("[pozri naživo](https://app.x.sk)", card)   # bare url → default label
        self.assertIn("[Money Gate](https://prod.x.sk/money-gate)", card)   # Label=URL deep link
        # a PR url but no live urls → no 🔗 line at all (PR alone is not shown)
        self.assertNotIn("🔗", self.notify.compose_autopilot_card(
            repo="o/x", pr="https://github.com/o/x/pull/9", tickets=[{"n": 1}]))

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
            # REGRESSION (odoo-slovnormal false ping): an agent ⏳ WORKING update
            # that NARRATES a past 529 must NOT be read as an api error.
            "Re-dispatched (the 529 did nothing — fresh start). Worker building the "
            "production workflow. ⏳ WORKING: staviam stav-workflow → demo → PROD. "
            "Ozvem sa.",
            # a bare "529" in prose, no status marker → still not an error
            "Re-dispatched after the 529 cleared, fresh start on the workflow.",
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
        # NOTE: m.Mock auto-creates EVERY attr truthy, so every cmd_notify
        # early-return flag (mention_prefix / channel_id / owner / mirror_owners /
        # autopilot_done) MUST be pinned False here — a new flag left unpinned hijacks
        # this test.
        args = m.Mock(run_card=True, autopilot_done=False, mention_prefix=False,
                       repo_name=False, newest_card=False,
                       backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                      record_question=False, edit_question=False,
                      channel_id=False, owner=False, mirror_owners=False,
                      body=None, run=None, repo="o/x", issue=5,
                      pr="https://h/pull/9", achieved="did the thing", result=None,
                      goal="Tunel občas vypadne", version="v9.9.9", merge_sha=None,
                      url=["Prod=https://montalu.sk/dash"], review="ok",
                      handoff=False, dedup_key=None, dry_run=False)
        captured = {}

        def fake_gh(*a, **k):
            # #382: full-authority `remaining` now unions THREE per-qual
            # `gh issue list --search ... --json number,...` queries
            # (`_obligation_quals()`), each returning a JSON array, not a
            # single `-q length` count string — every one of the three
            # returns the SAME 7-number array here, so the union still
            # dedupes to exactly 7 (matching the pre-#382 expectation).
            if "view" in a:
                return "Real Issue Title"
            return json.dumps([{"number": n} for n in range(1, 8)])

        def fake_send(body, **k):
            captured["body"] = body
            captured["dedup"] = k.get("dedup_key")
            return "sent"

        with m.patch.object(airuleset, "_gh_out", side_effect=fake_gh):
            with m.patch("notify.send", side_effect=fake_send):
                airuleset.cmd_notify(args)
        b = captured["body"]
        # 🎯 Cieľ = the worker's PLAIN --goal, NOT the technical gh title; header = #N only
        self.assertIn("🎯 **Cieľ:** Tunel občas vypadne", b)
        self.assertIn("🎫 **#5**", b)
        self.assertNotIn("Real Issue Title", b)
        self.assertIn("✅ **Dosiahnuté:** did the thing", b)
        self.assertIn("nasadené **v9.9.9**", b)   # deployed version on the 📦 line
        self.assertNotIn("PR #", b)               # bare PR number removed
        self.assertNotIn("/pull/9", b)            # PR link NOT rendered (user doesn't want it)
        # 🔗 line = the live "where to see it" url only
        self.assertIn("[Prod](https://montalu.sk/dash)", b)
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
                       repo_name=False, newest_card=False,
                       backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                          record_question=False, edit_question=False,
                      channel_id=False, owner=False, mirror_owners=False,
                          body=None, run=None, repo=repo, issue=606, pr=None,
                          achieved="a", result=None, goal="g", version=None,
                          merge_sha=None, url=None, review="ok", handoff=False,
                          dedup_key=None, dry_run=False)

        with m.patch.object(airuleset, "_gh_out",
                            side_effect=lambda *a, **k: "T" if "view" in a else "3"):
            with m.patch("notify.send", side_effect=fake_send):
                airuleset.cmd_notify(mk("zbynekdrlik/odoo-erp"))
                airuleset.cmd_notify(mk("odoo-erp"))  # re-dispatch, bare repo
        self.assertEqual(keys, ["odoo-erp#606", "odoo-erp#606"])  # identical key both times

    def test_run_card_records_the_card_map_on_send(self):
        # #298: capture the sent card's OWN message id -> repo/issue, so a
        # later Discord reply on it can reopen the ticket with the remark.
        import unittest.mock as m
        args = m.Mock(run_card=True, autopilot_done=False, mention_prefix=False,
                       repo_name=False, newest_card=False,
                       backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                      record_question=False, edit_question=False,
                      channel_id=False, owner=False, mirror_owners=False,
                      body=None, run=None, repo="zbynekdrlik/airuleset", issue=42,
                      pr=None, achieved="oprava retry logiky", result=None,
                      goal="Retry logika sa neopakuje", version="v1.2.3",
                      merge_sha=None, url=None, review="ok",
                      handoff=False, dedup_key=None, dry_run=False)
        recorded = {}

        def fake_send(body, **k):
            self.assertTrue(k.get("return_message_id"))
            return "sent", "555666777"

        def fake_record(message_id, channel, repo, issue, **k):
            recorded["args"] = (message_id, channel, repo, issue)
            return True

        with m.patch.object(airuleset, "_gh_out",
                            side_effect=lambda *a, **k: "T" if "view" in a else "7"):
            with m.patch("notify.send", side_effect=fake_send):
                with m.patch("notify.notification_channel", return_value="777001"):
                    with m.patch("notify.record_card_message",
                                 side_effect=fake_record):
                        airuleset.cmd_notify(args)
        self.assertEqual(recorded["args"],
                         ("555666777", "777001", "zbynekdrlik/airuleset", 42))

    def test_run_card_tolerates_a_bare_string_send_mock(self):
        # A test double for notify.send from BEFORE #298 may still return a
        # bare status STRING (the old contract) rather than the opt-in
        # (status, message_id) tuple -- must not crash.
        import unittest.mock as m
        args = m.Mock(run_card=True, autopilot_done=False, mention_prefix=False,
                       repo_name=False, newest_card=False,
                       backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                      record_question=False, edit_question=False,
                      channel_id=False, owner=False, mirror_owners=False,
                      body=None, run=None, repo="o/x", issue=5,
                      pr=None, achieved="a", result=None,
                      goal="g", version="v1", merge_sha=None,
                      url=None, review="ok",
                      handoff=False, dedup_key=None, dry_run=False)
        with m.patch.object(airuleset, "_gh_out",
                            side_effect=lambda *a, **k: "T" if "view" in a else "1"):
            with m.patch("notify.send", return_value="sent"):
                airuleset.cmd_notify(args)   # must not raise

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
        import io
        import contextlib
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

    def test_send_posts_to_per_owner_thread(self):
        # send() must POST to the OWNER's thread, not the shared channel, when a
        # per-owner thread is configured. Locks the routing end-to-end.
        import unittest.mock as m
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url

            class _R:
                def read(self):
                    return b""
            return _R()

        env = {"DISCORD_BOT_TOKEN": "x",
               "DISCORD_NOTIFICATION_CHANNEL_ID": "shared",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread",
               "DISCORD_NOTIFICATION_CHANNEL_MAREK": "mthread"}
        with tempfile.TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                with m.patch("notify.urllib.request.urlopen",
                             side_effect=fake_urlopen):
                    self.assertEqual(
                        self.notify.send("hi", env=env, owner="marek"), "sent")
        self.assertIn("/channels/mthread/messages", captured["url"])
        self.assertNotIn("shared", captured["url"])

    def test_send_mirrors_to_parallel_owner_thread(self):
        # david's notification must ALSO land in zbynek's thread with zbynek's
        # @mention — the persona-runs-parallel-to-a-real-person requirement. One
        # POST per target; the return status reflects the PRIMARY send only.
        import unittest.mock as m
        posts = []

        def fake_urlopen(req, timeout=None):
            posts.append((req.full_url, json.loads(req.data.decode())["content"]))

            class _R:
                def read(self):
                    return b""
            return _R()

        env = {"DISCORD_BOT_TOKEN": "x",
               "DISCORD_NOTIFICATION_CHANNEL_DAVID": "dthread",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread",
               "DISCORD_MENTION_DAVID": "90000",
               "DISCORD_MENTION_ZBYNEK": "10000",
               "DISCORD_MIRROR_DAVID": "zbynek"}
        with tempfile.TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                with m.patch("notify.urllib.request.urlopen",
                             side_effect=fake_urlopen):
                    r = self.notify.send("hi", env=env, owner="david")
        self.assertEqual(r, "sent")
        urls = {u for u, _ in posts}
        # BOTH threads received the message
        self.assertTrue(any("/channels/dthread/messages" in u for u in urls),
                        f"david thread not posted: {urls}")
        self.assertTrue(any("/channels/zthread/messages" in u for u in urls),
                        f"zbynek mirror thread not posted: {urls}")
        # each target got ITS OWN @mention (david=<@90000>, zbynek=<@10000>)
        d = next(c for u, c in posts if "dthread" in u)
        z = next(c for u, c in posts if "zthread" in u)
        self.assertTrue(d.startswith("<@90000> "), f"david mention wrong: {d!r}")
        self.assertTrue(z.startswith("<@10000> "), f"zbynek mention wrong: {z!r}")

    def test_send_mirror_skips_when_same_thread(self):
        # A mirror that resolves to the SAME thread as the primary must NOT double-post.
        import unittest.mock as m
        posts = []

        def fake_urlopen(req, timeout=None):
            posts.append(req.full_url)

            class _R:
                def read(self):
                    return b""
            return _R()

        env = {"DISCORD_BOT_TOKEN": "x",
               "DISCORD_NOTIFICATION_CHANNEL_ID": "shared",  # both fall back to shared
               "DISCORD_MIRROR_DAVID": "zbynek"}
        with tempfile.TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                with m.patch("notify.urllib.request.urlopen",
                             side_effect=fake_urlopen):
                    self.notify.send("hi", env=env, owner="david")
        self.assertEqual(len(posts), 1, f"double-posted to one thread: {posts}")

    def test_send_two_mirrors_sharing_a_channel_post_once(self):
        # #2: two mirror owners with NO per-owner thread both fall back to the shared
        # channel — the message must land there ONCE (dedup vs earlier mirrors, not
        # only vs the primary). david has its own thread, so david's thread + the
        # shared thread = exactly 2 posts.
        import unittest.mock as m
        posts = []

        def fake_urlopen(req, timeout=None):
            posts.append(req.full_url)

            class _R:
                def read(self):
                    return b""
            return _R()

        env = {"DISCORD_BOT_TOKEN": "x",
               "DISCORD_NOTIFICATION_CHANNEL_ID": "shared",
               "DISCORD_NOTIFICATION_CHANNEL_DAVID": "dthread",
               "DISCORD_MIRROR_DAVID": "zbynek marek"}   # neither has own thread
        with tempfile.TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                with m.patch("notify.urllib.request.urlopen",
                             side_effect=fake_urlopen):
                    self.notify.send("hi", env=env, owner="david")
        self.assertEqual(len(posts), 2, f"expected dthread + one shared: {posts}")
        self.assertTrue(any("dthread" in u for u in posts))
        self.assertEqual(sum(1 for u in posts if "/channels/shared/" in u), 1,
                         "shared channel must receive exactly one copy")

    def test_send_dry_run_shows_one_line_per_target(self):
        # dry-run mirrors the real fan-out: one line per target, primary first.
        import io
        import contextlib
        env = {"DISCORD_MENTION_DAVID": "90000",
               "DISCORD_MENTION_ZBYNEK": "10000",
               "DISCORD_NOTIFICATION_CHANNEL_DAVID": "dthread",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread",
               "DISCORD_MIRROR_DAVID": "zbynek"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = self.notify.send("BODY", env=env, owner="david", dry_run=True)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(r, "dry-run")
        self.assertEqual(lines, ["<@90000> BODY", "<@10000> BODY"])

    def test_send_unknown_owner_posts_to_shared(self):
        import unittest.mock as m
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url

            class _R:
                def read(self):
                    return b""
            return _R()

        env = {"DISCORD_BOT_TOKEN": "x",
               "DISCORD_NOTIFICATION_CHANNEL_ID": "shared",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"}
        with tempfile.TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                with m.patch("notify.urllib.request.urlopen",
                             side_effect=fake_urlopen):
                    self.assertEqual(
                        self.notify.send("hi", env=env, owner=""), "sent")
        self.assertIn("/channels/shared/messages", captured["url"])

    # --- end-to-end CLI ---------------------------------------------------
    def _env_home(self):
        home = tempfile.mkdtemp()
        d = Path(home) / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        (d / ".env").write_text(
            "DISCORD_MENTION_ZBYNEK=111222333\n"
            "DISCORD_MENTION_MAREK=444555666\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=shared999\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=zthread111\n"
            "DISCORD_NOTIFICATION_CHANNEL_MAREK=mthread222\n")
        return home

    def test_cli_mention_prefix(self):
        home = self._env_home()
        env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": "marek"}
        r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--mention-prefix"], capture_output=True, text=True,
                           env=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "<@444555666> ")

    def test_cli_channel_id(self):
        # The shell send path reads the resolved per-owner thread id from here.
        home = self._env_home()
        for owner, expected in (("marek", "mthread222"), ("zbynek", "zthread111")):
            env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": owner}
            r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                                "--channel-id"], capture_output=True, text=True,
                               env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, expected)
        # Unknown owner → shared fallback.
        env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": "nobody"}
        r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--channel-id"], capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout, "shared999")

    def test_cli_channel_id_kind_questions(self):
        # #296: --kind questions resolves the owner's SEPARATE questions
        # thread first, falling back to the SAME cascade --channel-id
        # already used before this flag existed.
        home = tempfile.mkdtemp()
        d = Path(home) / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        (d / ".env").write_text(
            "DISCORD_NOTIFICATION_CHANNEL_ID=shared999\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=zthread111\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=zqthread\n"
            "DISCORD_NOTIFICATION_CHANNEL_MAREK=mthread222\n")
        env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": "zbynek"}
        r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--channel-id", "--kind", "questions"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "zqthread")
        # --kind omitted (the pre-#296 shape) is completely unaffected.
        r2 = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--channel-id"], capture_output=True, text=True,
                           env=env)
        self.assertEqual(r2.stdout, "zthread111")
        # marek has no _Q thread configured yet -> falls back to HIS normal
        # thread (never the shared channel just because _Q is unconfigured).
        env_marek = {**os.environ, "HOME": home,
                    "AIRULESET_NOTIFY_OWNER": "marek"}
        r3 = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--channel-id", "--kind", "questions"],
                           capture_output=True, text=True, env=env_marek)
        self.assertEqual(r3.stdout, "mthread222")

    def test_cli_body_honors_owner_name_override(self):
        # #334: an internal/headless caller (codex-bridge's ad-hoc-prod-write
        # ping) must be able to PIN an explicit owner for a --body send,
        # bypassing tmux auto-detection entirely -- the real fix for the
        # claude-david misroute regression. Root cause: --owner-name already
        # existed as a global CLI flag, but --body never consumed it, so
        # EVERY --body caller was forced through resolve_owner()'s tmux
        # fallback with no override available at all. Stripping
        # AIRULESET_NOTIFY_OWNER/TMUX makes this deterministic regardless
        # of whatever tmux state this box's own dev session happens to sit
        # in (per the sibling documenting test above).
        home = self._env_home()
        env = {**os.environ, "HOME": home}
        env.pop("AIRULESET_NOTIFY_OWNER", None)
        env.pop("TMUX", None)
        r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--body", "test ping", "--owner-name", "marek",
                            "--dry-run"], capture_output=True, text=True,
                           env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<@444555666> test ping", r.stdout)   # marek's own mention
        self.assertNotIn("<@111222333>", r.stdout)           # never zbynek's

    def test_cli_body_owner_name_normalizes_denormalized_input(self):
        # #334 adversarial-review MINOR-1: the normalization step is
        # mutation-invisible when every test only ever passes an
        # already-normalized value. A denormalized --owner-name (mixed
        # case + trailing space, mirroring test_cli_provision_question_
        # thread_normalizes_owner_name's own shape) must still resolve to
        # marek's own mention -- not silently miss the env key and fall
        # to the shared channel.
        home = self._env_home()
        env = {**os.environ, "HOME": home}
        env.pop("AIRULESET_NOTIFY_OWNER", None)
        env.pop("TMUX", None)
        r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--body", "test ping", "--owner-name", "Marek ",
                            "--dry-run"], capture_output=True, text=True,
                           env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<@444555666> test ping", r.stdout)

    def test_cli_body_owner_name_that_normalizes_to_empty_is_refused(self):
        # #334 adversarial-review MINOR-2: a NON-EMPTY --owner-name that
        # normalizes to the empty string (e.g. punctuation-only) must be
        # REFUSED loudly, never silently mangled into owner="" -- which
        # would skip resolve_owner() entirely (owner="" is not None) and
        # send mention-less to the shared channel, even overriding an
        # otherwise-correct AIRULESET_NOTIFY_OWNER. Mirrors #198's
        # "validate and refuse, never mangle" rule and matches
        # --provision-question-thread's own loud-failure shape.
        home = self._env_home()
        env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": "marek"}
        env.pop("TMUX", None)
        r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--body", "test ping", "--owner-name", "!!!",
                            "--dry-run"], capture_output=True, text=True,
                           env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("owner-name", r.stderr.lower())

    def test_cli_body_without_owner_name_falls_back_to_resolve_owner(self):
        # Non-regression: omitting --owner-name keeps today's EXACT
        # behavior -- the fix is purely additive, never a default change.
        home = self._env_home()
        env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": "zbynek"}
        r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--body", "hello", "--dry-run"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<@111222333> hello", r.stdout)

    def test_cli_provision_question_thread_success(self):
        args = m.Mock(provision_question_thread=True, find_only=False,
                      autopilot_done=False,
                      mention_prefix=False, repo_name=False, newest_card=False,
                      backfill_digest=False, record_question=False,
                      edit_question=False, channel_id=False, owner=False,
                      mirror_owners=False, run_card=False, api_error=False,
                      body=None, owner_name="zbynek")
        import io
        import contextlib
        with m.patch("notify.provision_question_thread",
                     return_value="newqid") as fake:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_notify(args)
        self.assertEqual(buf.getvalue(), "newqid")
        fake.assert_called_once_with("zbynek")

    def test_cli_provision_question_thread_defaults_to_resolved_owner(self):
        args = m.Mock(provision_question_thread=True, find_only=False,
                      autopilot_done=False,
                      mention_prefix=False, repo_name=False, newest_card=False,
                      backfill_digest=False, record_question=False,
                      edit_question=False, channel_id=False, owner=False,
                      mirror_owners=False, run_card=False, api_error=False,
                      body=None, owner_name=None)
        with m.patch("notify.resolve_owner", return_value="marek"), \
                m.patch("notify.provision_question_thread",
                       return_value="q2") as fake:
            airuleset.cmd_notify(args)
        fake.assert_called_once_with("marek")

    def test_cli_provision_question_thread_failure_exits_nonzero(self):
        # #330 round-2 adversarial review MAJOR: this test predates #330's
        # own F7 fix, which added a real `log_delivery("provision-failed",
        # ...)` call to exactly this failure path — an UNMOCKED
        # log_delivery here appends a real line to THIS box's live
        # ~/.claude/notify-delivery.log on every full-suite run (caught
        # live: 7 fake "provision-failed key=zbynek" lines had already
        # landed there before this fix). Mirrors the sibling
        # ...failure_logs_provision_failed test's own isolation.
        args = m.Mock(provision_question_thread=True, find_only=False,
                      autopilot_done=False,
                      mention_prefix=False, repo_name=False, newest_card=False,
                      backfill_digest=False, record_question=False,
                      edit_question=False, channel_id=False, owner=False,
                      mirror_owners=False, run_card=False, api_error=False,
                      body=None, owner_name="zbynek")
        with m.patch("notify.provision_question_thread", return_value=""), \
                m.patch("notify.log_delivery"):
            with self.assertRaises(SystemExit) as cm:
                airuleset.cmd_notify(args)
            self.assertEqual(cm.exception.code, 1)

    def test_cli_provision_question_thread_failure_logs_provision_failed(self):
        # #330 F7: the AUTOMATIC background self-heal runs fully detached
        # (stdout/stderr both DEVNULL'd) — before this, a failed self-heal
        # attempt left NO trace anywhere, forever, even though the loud
        # "fallback" line already told the operator one was attempted.
        args = m.Mock(provision_question_thread=True, find_only=False,
                      autopilot_done=False,
                      mention_prefix=False, repo_name=False, newest_card=False,
                      backfill_digest=False, record_question=False,
                      edit_question=False, channel_id=False, owner=False,
                      mirror_owners=False, run_card=False, api_error=False,
                      body=None, owner_name="zbynek")
        with m.patch("notify.provision_question_thread", return_value=""), \
                m.patch("notify.log_delivery") as fake_log:
            with self.assertRaises(SystemExit):
                airuleset.cmd_notify(args)
        fake_log.assert_called_once()
        call_args, call_kwargs = fake_log.call_args
        self.assertEqual(call_args[0], "provision-failed")
        self.assertEqual(call_kwargs.get("key"), "zbynek")

    def test_cli_provision_question_thread_find_only_never_creates(self):
        # #330 F3: --find-only is the flag `_spawn_provision_question_thread`
        # itself passes — the AUTOMATIC background self-heal must never
        # spin up a brand-new Discord thread on its own.
        args = m.Mock(provision_question_thread=True, find_only=True,
                      autopilot_done=False,
                      mention_prefix=False, repo_name=False, newest_card=False,
                      backfill_digest=False, record_question=False,
                      edit_question=False, channel_id=False, owner=False,
                      mirror_owners=False, run_card=False, api_error=False,
                      body=None, owner_name="zbynek")
        import io
        import contextlib
        with m.patch("notify.provision_question_thread",
                     return_value="foundid") as fake:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_notify(args)
        self.assertEqual(buf.getvalue(), "foundid")
        fake.assert_called_once_with("zbynek", create=False)

    def test_cli_provision_question_thread_default_omits_create_kwarg(self):
        # The pre-#330 default path must call provision_question_thread
        # with EXACTLY ONE positional arg, byte-identical to before —
        # never a `create=True` kwarg that changes the call SIGNATURE for
        # every existing caller/mock.
        args = m.Mock(provision_question_thread=True, find_only=False,
                      autopilot_done=False,
                      mention_prefix=False, repo_name=False, newest_card=False,
                      backfill_digest=False, record_question=False,
                      edit_question=False, channel_id=False, owner=False,
                      mirror_owners=False, run_card=False, api_error=False,
                      body=None, owner_name="zbynek")
        with m.patch("notify.provision_question_thread",
                     return_value="q1") as fake:
            airuleset.cmd_notify(args)
        fake.assert_called_once_with("zbynek")

    def test_cli_provision_question_thread_normalizes_owner_name(self):
        # Adversarial-review THEORETICAL finding: an un-normalized
        # --owner-name typo (mixed case, a trailing space) must resolve to
        # the SAME normalized key resolve_owner() itself would produce —
        # otherwise it silently creates a real Discord thread and persists
        # it under a DEAD .env key nothing ever reads.
        args = m.Mock(provision_question_thread=True, find_only=False,
                      autopilot_done=False,
                      mention_prefix=False, repo_name=False, newest_card=False,
                      backfill_digest=False, record_question=False,
                      edit_question=False, channel_id=False, owner=False,
                      mirror_owners=False, run_card=False, api_error=False,
                      body=None, owner_name="Zbynek ")
        with m.patch("notify.provision_question_thread",
                     return_value="q1") as fake:
            airuleset.cmd_notify(args)
        fake.assert_called_once_with("zbynek")

    def test_cli_owner(self):
        # `notify --owner` lets the shell hook resolve ONCE and force the same owner
        # onto both --mention-prefix and --channel-id (so they can never disagree).
        home = self._env_home()
        env = {**os.environ, "HOME": home, "AIRULESET_NOTIFY_OWNER": "Zbynek"}
        r = subprocess.run([sys.executable, str(self.AIRULESET), "notify",
                            "--owner"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "zbynek")          # normalized like resolve_owner

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
        # the channel/THREAD target is owner-aware via the single source of truth,
        # NOT a direct grep of the shared id (that mixed both owners into one thread)
        self.assertIn("notify --channel-id", src)
        self.assertNotIn("DISCORD_NOTIFICATION_CHANNEL_ID", src)
        # owner resolved ONCE and forced onto both calls so the @mention and the
        # per-owner thread can never disagree (the reviewer's flagged concern)
        self.assertIn("notify --owner", src)
        self.assertIn("AIRULESET_NOTIFY_OWNER", src)
        # both hooks delegate to it (no duplicated curl)
        self.assertIn("notify-discord-send.sh", self.IDLE_HOOK.read_text())
        pending = (airuleset.REPO_DIR / "hooks" / "notify-discord-pending.sh").read_text()
        self.assertIn("notify-discord-send.sh", pending)
        # #296: the --channel-id call threads a --kind so a ❓ can resolve a
        # SEPARATE questions thread from a ✅/card/api-error ping.
        self.assertIn("--kind", src)

    def test_send_hook_selects_questions_channel_only_for_question_emoji(self):
        # #296 end-to-end: ND_EMOJI="❓" must resolve --channel-id via
        # --kind questions; every other emoji (✅, and anything future) keeps
        # --kind default — the owner's EXISTING claude-<owner> thread. A real
        # python3 SHIM on PATH logs every invocation's argv then execs the
        # REAL python3 with the SAME args, so this exercises the actual
        # airuleset.py CLI resolution (not a guess about what it would do),
        # with zero network I/O (--channel-id/--mention-prefix/--owner are
        # pure reads; DISCORD_NOTIFY_DRYRUN=1 keeps the curl paths inert too).
        home = tempfile.mkdtemp()
        shimdir = tempfile.mkdtemp()
        log = Path(home) / "py3.log"
        real_py3 = sys.executable
        shim = Path(shimdir) / "python3"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%%s\\n' \"$*\" >> %s\n"
            "exec %s \"$@\"\n" % (str(log), real_py3))
        shim.chmod(0o755)
        base_env = {**os.environ, "PATH": str(shimdir) + os.pathsep + os.environ["PATH"],
                   "HOME": home, "AIRULESET_NOTIFY_OWNER": "zbynek",
                   "DISCORD_NOTIFY_DRYRUN": "1"}

        def _channel_kinds():
            lines = log.read_text().splitlines() if log.exists() else []
            calls = [ln for ln in lines if "--channel-id" in ln]
            kinds = []
            for ln in calls:
                toks = ln.split()
                kinds.append(toks[toks.index("--kind") + 1]
                             if "--kind" in toks else None)
            return kinds

        subprocess.run(["bash", str(self.SEND_HOOK)], input="", text=True,
                       capture_output=True,
                       env={**base_env, "ND_EMOJI": "❓", "ND_TEXT": "t",
                            "ND_CWD": "/tmp"})
        self.assertEqual(_channel_kinds(), ["questions"], log.read_text())

        log.unlink()
        subprocess.run(["bash", str(self.SEND_HOOK)], input="", text=True,
                       capture_output=True,
                       env={**base_env, "ND_EMOJI": "✅", "ND_TEXT": "t",
                            "ND_CWD": "/tmp"})
        self.assertEqual(_channel_kinds(), ["default"], log.read_text())

    def test_send_hook_selects_questions_kind_for_mirror_targets_too(self):
        # Coverage gap the adversarial review flagged: a mirrored persona
        # (DISCORD_MIRROR_<OWNER>) must get its OWN --kind questions
        # resolution too, not just the primary owner — emit_one() is called
        # once per target with the SAME $KIND, so every --channel-id call
        # this sweep makes (one per target: primary + each mirror) must
        # show --kind questions for a ❓ ping.
        home = tempfile.mkdtemp()
        shimdir = tempfile.mkdtemp()
        log = Path(home) / "py3.log"
        real_py3 = sys.executable
        shim = Path(shimdir) / "python3"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%%s\\n' \"$*\" >> %s\n"
            "exec %s \"$@\"\n" % (str(log), real_py3))
        shim.chmod(0o755)
        d = Path(home) / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        (d / ".env").write_text("DISCORD_MIRROR_ZBYNEK=marek\n")
        env = {**os.environ, "PATH": str(shimdir) + os.pathsep + os.environ["PATH"],
              "HOME": home, "AIRULESET_NOTIFY_OWNER": "zbynek",
              "DISCORD_NOTIFY_DRYRUN": "1", "ND_EMOJI": "❓", "ND_TEXT": "t",
              "ND_CWD": "/tmp"}
        subprocess.run(["bash", str(self.SEND_HOOK)], input="", text=True,
                       capture_output=True, env=env)
        lines = log.read_text().splitlines() if log.exists() else []
        calls = [ln for ln in lines if "--channel-id" in ln]
        # primary (zbynek) + mirror (marek) = 2 --channel-id calls this sweep
        self.assertEqual(len(calls), 2, log.read_text())
        for ln in calls:
            toks = ln.split()
            self.assertIn("--kind", toks, ln)
            self.assertEqual(toks[toks.index("--kind") + 1], "questions", ln)


# A pane IDLE at a free `❯` prompt (turn ended, safe to type a nudge). The real prompt
# renders as `❯`+NBSP → `.strip()` == "❯". No _WAITING_RX footer, no session-limit banner.
_IDLE_PANE = ("● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n"
              "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")
# A pane actively running a FOREGROUND agent — spinner, no free `❯` (typing interrupts).
_BUSY_PANE = ("● Validate issue #233\n  ⎿ running…\n"
              "✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n")


class _FakeTmux:
    """Stand-in for the watchdog's `run` (tmux exec). Answers list-panes /
    capture-pane from canned data and records every send-keys argv."""

    def __init__(self, panes="", captures=None, modes=None, owners=None,
                 default_capture=_IDLE_PANE):
        self.panes = panes
        self.captures = captures or {}
        # Panes with no explicit capture default to an IDLE `❯` prompt — i.e. typeable —
        # so a transcript-stall test fires the nudge (the pre-#233-fix assumption). A
        # busy-pane test passes default_capture=_BUSY_PANE (or an explicit captures= map).
        self.default_capture = default_capture
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
            return self.captures.get(pid, self.default_capture)
        if argv[:2] == ["tmux", "send-keys"]:
            self.sent.append(argv)
            return ""
        return ""

    def continues_sent(self):
        # how many `send-keys -l continue` (the literal text, not the Enter) fired
        return sum(1 for a in self.sent if "-l" in a and "continue" in a)

    def selfchecks_sent(self):
        # how many job-4 SELF-CHECK nudges (literal text containing "stuck-check") fired
        return sum(1 for a in self.sent if "-l" in a and any("stuck-check" in x for x in a))


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
        # Isolate the usage cache so check_usage's write can NEVER clobber the real
        # ~/.claude/airuleset-usage-cache.json during the suite (it did once).
        self._orig_usage_cache = self.w.usage._USAGE_CACHE_PATH
        self.w.usage._USAGE_CACHE_PATH = str(Path(self.tmp) / "usage-cache.json")

    def tearDown(self):
        self.w.usage._USAGE_CACHE_PATH = self._orig_usage_cache

    def _send(self, body, owner=None, dedup_key=None, dry_run=False):
        self.pings.append((body, dedup_key, owner))
        return "sent"

    # real CC session ids are UUIDs (transcript stems) — the state cleanup
    # keys on that shape, so the fixture must match the real contract
    _SID = "5e55abc0-51d0-4a5e-9f1e-00000000abcd"

    _ERR = {"type": "assistant", "isApiErrorMessage": True,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "API Error: 529 Overloaded"}]}}
    _OK = {"type": "assistant", "isApiErrorMessage": False,
           "message": {"role": "assistant", "content": [{"type": "text", "text": "Hotovo."}]}}
    _SENT = {"type": "assistant",
             "message": {"role": "assistant", "content": [{"type": "text",
                         "text": "No response requested."}]}}
    # --- issue #484 recovery fixtures: a subagent that RECOVERED from an api-error
    # (SendMessage resume → live Bash poll loop) writes these AFTER the append-only
    # api-error line. A pure tool_use assistant entry has EMPTY text ("" ∈ _SENTINELS)
    # and a tool_result is a `user` entry — both were silently skipped by the old
    # walk-back, letting it re-find the historical error forever.
    _TOOLUSE = {"type": "assistant", "isApiErrorMessage": False,
                "message": {"role": "assistant",
                            "content": [{"type": "tool_use", "name": "Bash",
                                         "input": {"command": "sleep 240; gh run view"}}]}}
    _TRESULT = {"type": "user",
                "message": {"role": "user",
                            "content": [{"type": "tool_result",
                                         "content": "PENDING in_progress"}]}}
    _RESUME = {"type": "user",
               "message": {"role": "user",
                           "content": [{"type": "text", "text": "pokracuj"}]}}

    def _transcript(self, cwd, entries, age_s, now):
        d = self.projects / self.w.encode_project_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / (self._SID + ".jsonl")
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

    # --- issue #484: a HISTORICAL api-error that has SINCE recovered must NOT be
    # reported. The append-only jsonl keeps the error line forever, so job 1b's
    # `_nudge_dying_subagent` would stuck-check ping the supervisor endlessly. The
    # fix reads the RIGHT signal: recency of the error vs later activity (mirrors the
    # sibling reader `transcript_text_toolcall_stall`). ------------------------------
    def test_transcript_last_error_recovered_via_tool_use_is_empty(self):
        # api-error, then the resumed agent's Bash poll (tool_use, empty text) → healthy
        p = self._transcript("/x/p", [self._ERR, self._TOOLUSE], 600, 1_000_000)
        self.assertEqual(self.w.transcript_last_error(p), "")

    def test_transcript_last_error_recovered_via_tool_result_tail_is_empty(self):
        # newest entry is a tool_result (user), NO tool_use between it and the error,
        # so this isolates the `user → progressed` branch (a _TOOLUSE middle entry
        # would satisfy _entry_has_tool_use instead and mask the branch under test).
        p = self._transcript("/x/p", [self._ERR, self._TRESULT], 600, 1_000_000)
        self.assertEqual(self.w.transcript_last_error(p), "")

    def test_transcript_last_error_484_endless_ping_scenario_is_empty(self):
        # The exact montalu 2026-08-15 shape: api-error(429) → SendMessage resume →
        # several live Bash poll chunks. Old code returned the 429 text forever.
        entries = [self._ERR, self._RESUME, self._TOOLUSE, self._TRESULT,
                   self._TOOLUSE, self._TRESULT]
        p = self._transcript("/x/p", entries, 600, 1_000_000)
        self.assertEqual(self.w.transcript_last_error(p), "")

    def test_transcript_last_error_genuine_stall_after_toolcalls_still_detected(self):
        # The agent DID work (tool_use), THEN api-errored as its last real turn →
        # this is a genuine current stall and MUST still be reported.
        p = self._transcript("/x/p", [self._TOOLUSE, self._TRESULT, self._ERR], 600, 1_000_000)
        self.assertIn("529", self.w.transcript_last_error(p))

    def test_transcript_last_error_genuine_stall_with_trailing_system_still_detected(self):
        # A trailing `system` (hook noise) entry after the error must not mask it.
        sys_entry = {"type": "system", "content": "hook fired"}
        p = self._transcript("/x/p", [self._ERR, sys_entry], 600, 1_000_000)
        self.assertIn("529", self.w.transcript_last_error(p))

    def test_transcript_last_error_genuine_stall_with_trailing_bookkeeping_still_detected(self):
        # CC appends NON-conversational bookkeeping entries (queue-operation, ai-title,
        # file-history-snapshot, mode, permission-mode, …) that are NOT progress —
        # a genuine api-error stall with one of these newest must STILL be reported
        # (round-1 review MAJOR: returning '' on any non-assistant tail was a silent
        # false negative in the auto-resume path).
        for bk in ("queue-operation", "ai-title", "file-history-snapshot",
                   "mode", "permission-mode", "pr-link"):
            p = self._transcript("/x/p", [self._ERR, {"type": bk}], 600, 1_000_000)
            self.assertIn("529", self.w.transcript_last_error(p),
                          "genuine stall masked by trailing %s entry" % bk)

    # --- issue #484 round-A 🔴: a PLAIN-TEXT `user` entry is NOT proof of recovery.
    # Job 1's OWN injected `continue` nudge lands in the transcript as a bare-text
    # `user` entry the instant it is typed, whether or not it woke the session; and a
    # human/resume prompt the session has not yet acted on is equally not-yet-recovery.
    # Reading any `user` tail as "recovered" returned '' here → job 1 skipped
    # `stalled.add(key)`, the sweep cleanup wiped the episode, and the #175 back-off
    # reset to nudge#1 every time CC's retry re-wrote the error line — the SAME endless
    # ping, relocated from the subagent path (job 1b) to the main-session path (job 1).
    # Only a `user` entry carrying a `tool_result` (the harness actually RAN a tool)
    # counts as recovery. ---------------------------------------------------------------
    def test_transcript_last_error_plaintext_user_nudge_does_not_mask_stall(self):
        # api-error, then a bare-text user entry (job 1's `continue` nudge / an
        # un-acted-on resume prompt) — no tool activity → STILL stalled, must report.
        p = self._transcript("/x/p", [self._ERR, self._RESUME], 600, 1_000_000)
        self.assertIn("529", self.w.transcript_last_error(p))

    def test_transcript_last_error_plaintext_user_then_bookkeeping_still_stalled(self):
        # the nudge (plain-text user) buried under trailing bookkeeping, still no
        # genuine tool_result/tool_use activity anywhere after the error → still stalled.
        p = self._transcript("/x/p", [self._ERR, self._RESUME, {"type": "queue-operation"}],
                             600, 1_000_000)
        self.assertIn("529", self.w.transcript_last_error(p))

    def test_transcript_last_error_recovered_via_tool_result_after_plaintext_user_is_empty(self):
        # nudge (plain-text user) that DID wake the session → the harness then ran a
        # tool and fed a tool_result back → genuine progress → not stalled.
        p = self._transcript("/x/p", [self._ERR, self._RESUME, self._TRESULT], 600, 1_000_000)
        self.assertEqual(self.w.transcript_last_error(p), "")

    def test_transcript_last_error_genuine_stall_survives_long_bookkeeping_tail(self):
        # round-B 🔵: the bookkeeping-skip robustness is only bounded by the tail
        # window. CC emits many hook/bookkeeping writes per turn, so a genuine api-error
        # stall can sit buried under a burst of >60 of them. The reader scans the SAME
        # 200-line window as the sibling transcript_text_toolcall_stall — the default
        # 60-line window pushed the error out of view and silently under-reported it.
        tail = [{"type": "file-history-snapshot"} for _ in range(100)]
        p = self._transcript("/x/p", [self._ERR] + tail, 600, 1_000_000)
        self.assertIn("529", self.w.transcript_last_error(p))

    def test_list_claude_panes_dedups_and_filters(self):
        fake = _FakeTmux(panes="%5\tclaude\t/devel/a\n%5\tclaude\t/devel/a\n"
                               "%6\tbash\t/devel/b\n%7\tclaude\t/devel/c\n")
        self.assertEqual(self.w.list_claude_panes(fake), [("%5", "/devel/a"), ("%7", "/devel/c")])

    # --- decide state machine ------------------------------------------------
    def _dec(self, st, key, h, now, seed=None):
        return self.w.decide(st, key, h, now, grace=300, interval=300, max_nudges=3,
                             first_seen_seed=seed)

    def test_decide_lifecycle_fresh_stall(self):
        # a FRESH stall (seed=now) waits a full grace before the first nudge
        st, now = {}, 1_000_000
        a, e = self._dec(st, "k", "h", now, seed=now)
        self.assertEqual(a, "wait")
        st["k"] = e
        a, e = self._dec(st, "k", "h", now + 100, seed=now)
        self.assertEqual(a, "wait")
        st["k"] = e
        a, e = self._dec(st, "k", "h", now + 300, seed=now)    # grace elapsed → nudge #1
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 1)
        st["k"] = e
        a, e = self._dec(st, "k", "h", now + 600, seed=now)    # +interval → #2
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 2)
        st["k"] = e
        a, e = self._dec(st, "k", "h", now + 900, seed=now)    # #3
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 3)
        st["k"] = e
        a, e = self._dec(st, "k", "h", now + 1500, seed=now)   # +600 (widened) → #4
        self.assertEqual(a, "nudge")                # (#175) still nudges — never gives up
        self.assertEqual(len(e["nudges"]), 4)
        self.assertTrue(e["escalated"], "one-shot give-up flag set from nudge #4 on")
        st["k"] = e
        a, e = self._dec(st, "k", "h", now + 2700, seed=now)   # +1200 (widened) → #5
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 5)
        self.assertTrue(e["escalated"], "escalated stays True, no repeat give-up")

    def test_decide_backoff_never_gives_up_on_a_multi_hour_stall(self):
        # #175: a multi-hour upstream 529 storm used to strand a session — the
        # OLD policy covered ~15-20 min (3 nudges @ grace/interval=300s) then
        # returned 'escalate' once and 'noop' forever after, even though the
        # session was still stalled hours later. MAX_NUDGES must stop being a
        # hard end: past it, decide() keeps returning 'nudge' INDEFINITELY, at
        # a WIDENING interval (300, 300, 300, then 600 / 1200 / 1800 / 1800 /
        # ...), covering a stall well past the 2-hour mark instead of going
        # silent at ~15 min.
        st, now = {}, 1_000_000
        a, e = self._dec(st, "k", "h", now, seed=now)
        self.assertEqual(a, "wait")
        st["k"] = e
        schedule = [300, 300, 300, 600, 1200, 1800, 1800, 1800]   # nudges #1..#8
        cursor = now
        for i, gap in enumerate(schedule, start=1):
            cursor += gap
            a, e = self._dec(st, "k", "h", cursor, seed=now)
            self.assertEqual(a, "nudge",
                              "nudge #%d must still fire at +%ds (elapsed since "
                              "first stall) — decide() must not give up" % (i, cursor - now))
            self.assertEqual(len(e["nudges"]), i)
            if i < 4:
                self.assertFalse(e["escalated"], "no give-up ping before nudge #4")
            else:
                self.assertTrue(e["escalated"], "give-up ping flag set from nudge #4 on")
            st["k"] = e
        # total elapsed here is 8100s (2h15m) — well past the 2-hour incident
        # window (gatekeeper 4h28m / simap 6h31m) — and it is STILL nudging.
        self.assertGreater(cursor - now, 2 * 3600)

    def test_decide_withholds_the_nudge_before_the_widened_interval_elapses(self):
        # #175 F3: the flagship widening test above only ever calls decide()
        # AT the cumulative due instants — a constant-300s (no-widening)
        # schedule fires at every one of those instants too (e.g. 1500-900 =
        # 600 >= 300), so it has NO TEETH proving the interval actually
        # widens, only that decide() never permanently gives up. Calling
        # decide() BETWEEN two consecutive due instants must WITHHOLD the
        # nudge — proving the interval really doubled — at each widening
        # step: 300->600 (nudge #4), 600->1200 (nudge #5), and 1200->1800
        # capped (nudge #6, where the uncapped exponential would be 2400).
        st, now = {}, 1_000_000
        a, e = self._dec(st, "k", "h", now, seed=now)
        self.assertEqual(a, "wait")
        st["k"] = e
        for t in (now + 300, now + 600, now + 900):     # nudges #1, #2, #3
            a, e = self._dec(st, "k", "h", t, seed=now)
            self.assertEqual(a, "nudge")
            st["k"] = e
        last = now + 900                                 # nudge #3 landed here

        # nudge #4's real interval is 600s (2x base) — a call at the OLD,
        # un-widened 300s interval must be withheld.
        a, e = self._dec(st, "k", "h", last + 300, seed=now)
        self.assertEqual(a, "wait", "nudge #4 must NOT fire at the "
                         "un-widened 300s interval — the interval must "
                         "have doubled")
        a, e = self._dec(st, "k", "h", last + 600, seed=now)
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 4)
        st["k"] = e
        last += 600

        # nudge #5's real interval is 1200s (4x base) — half that must be
        # withheld.
        a, e = self._dec(st, "k", "h", last + 600, seed=now)
        self.assertEqual(a, "wait", "nudge #5 must NOT fire at half its "
                         "real (1200s) interval")
        a, e = self._dec(st, "k", "h", last + 1200, seed=now)
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 5)
        st["k"] = e
        last += 1200

        # nudge #6's real interval hits the CAP (1800s — the uncapped
        # exponential would be 300*2**3=2400) — a call before the cap must
        # be withheld.
        a, e = self._dec(st, "k", "h", last + 1200, seed=now)
        self.assertEqual(a, "wait", "nudge #6 must NOT fire before the "
                         "capped 1800s interval elapses")
        a, e = self._dec(st, "k", "h", last + 1800, seed=now)
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 6)

    def test_backoff_cap_seconds_is_pinned_at_1800(self):
        # #175 F3: BACKOFF_CAP_SECONDS had no dedicated assertion at all —
        # 1e9, 300, or 7200 all left the whole suite green. Pin the ACTUAL
        # shipped value AND prove decide() enforces it as a hard ceiling,
        # not merely an unused constant, even when the uncapped exponential
        # would be far larger.
        self.assertEqual(self.w.BACKOFF_CAP_SECONDS, 1800)
        st, now = {}, 1_000_000
        # n=10 nudges → step=8 → uncapped interval = 300*2**8 = 76800s.
        # Only the LAST entry's timestamp and the COUNT matter to decide().
        st["k"] = {"hash": "h", "first_seen": now, "nudges": [now] * 10,
                  "escalated": True}
        a, e = self._dec(st, "k", "h", now + 1799, seed=now)
        self.assertEqual(a, "wait",
                         "must not fire one second before the capped interval")
        a, e = self._dec(st, "k", "h", now + 1800, seed=now)
        self.assertEqual(a, "nudge",
                         "must fire exactly at the capped interval, not later")

    def test_decide_already_stale_nudges_on_first_sighting(self):
        # seed older than grace (the rate-limit / presenter case once detected) →
        # the first `continue` goes out immediately, no extra grace wait
        st, now = {}, 1_000_000
        a, e = self._dec(st, "k", "h", now, seed=now - 600)
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 1)

    def test_decide_new_error_hash_resets(self):
        st = {"k": {"hash": "old", "first_seen": 1, "nudges": [1, 2, 3], "escalated": True}}
        a, e = self._dec(st, "k", "NEWHASH", 1_000_000, seed=1_000_000)  # fresh seed
        self.assertEqual(a, "wait")                 # new error, not yet grace-old
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
        self.assertTrue(any("nudge#1" in ln for ln in logs))

    def test_run_once_apierror_skipped_when_pane_busy(self):
        # #233 uniform guard: an api-error flag on the last entry normally means CC
        # aborted the turn (pane idle at `❯`). But if the user MANUALLY resumed within
        # the idle window, a foreground turn/agent is running (busy pane, no free `❯`)
        # and typing `continue` would INTERRUPT it. A busy pane → skip, no keystroke,
        # no ping, no retry burned.
        now = 1_000_000
        cwd = "/devel/projbusy"
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", default_capture=_BUSY_PANE)
        logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               grace=300, interval=300, max_nudges=3)
        self.assertEqual(fake.continues_sent(), 0, "must NOT type into a running agent")
        self.assertEqual(self.pings, [], "busy pane = not stalled → no ping")
        self.assertTrue(any("skip busy-pane (api-error)" in ln for ln in logs))

    # A pane genuinely IDLE at `❯` (turn ended, safe to type) but whose input box
    # still holds a FOREIGN DRAFT — the #176 gatekeeper incident shape. NOT bare, so
    # `pane_at_idle_prompt` (bare_only=True) reads it as "not idle", identically to a
    # real foreground turn — that misread is the root cause #176 fixes.
    _DRAFT_PANE = "❯\xa0nechať ako je\n"

    def test_run_once_apierror_revives_idle_with_draft_via_stash(self):
        # #176 item 1: idle-at-`❯`-with-a-draft must NOT be treated as busy — the
        # session genuinely IS idle. `_classify_boundary` tells it apart from a real
        # foreground turn and delivery goes through `deliver_with_stash` (the
        # verified idle-with-draft protocol) instead of a raw `continue` typed over
        # the draft. The skip must NOT be logged, and exactly one nudge lands.
        now = 1_000_000
        cwd = "/devel/projdraft"
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         default_capture=self._DRAFT_PANE)
        calls = []

        def _fake_stash(pid, text, run, captured=None, logs=None):
            calls.append((pid, text))
            return True
        with m.patch.object(self.w, "deliver_with_stash", side_effect=_fake_stash):
            logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                                   projects_dir=self.projects, state_path=self.state,
                                   grace=300, interval=300, max_nudges=3)
        self.assertEqual(len(calls), 1,
                         "must deliver via the verified stash protocol, not refuse")
        self.assertEqual(calls[0], ("%5", "continue"))
        self.assertEqual(fake.continues_sent(), 0,
                         "never a raw keystroke over the user's draft")
        self.assertFalse(any("skip busy-pane" in ln for ln in logs), logs)
        self.assertTrue(any("nudge#1" in ln for ln in logs), logs)
        self.assertEqual(len(self.pings), 1, "a first nudge still pings like any stall")

    def test_run_once_apierror_aborted_stash_does_not_burn_a_retry(self):
        # #176 item 1: an ABORTED stash (deliver_with_stash returns False — the
        # draft moved, a live turn started, the stash slot was already occupied,
        # ...) must NOT advance the nudge/backoff state — the next poll retries
        # from scratch instead of having silently "used up" an attempt.
        now = 1_000_000
        cwd = "/devel/projdraftabort"
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         default_capture=self._DRAFT_PANE)
        with m.patch.object(self.w, "deliver_with_stash", return_value=False):
            logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                                   projects_dir=self.projects, state_path=self.state,
                                   grace=300, interval=300, max_nudges=3)
        self.assertEqual(fake.continues_sent(), 0)
        self.assertEqual(self.pings, [], "an aborted stash must not ping either")
        self.assertTrue(any("stash-abort" in ln for ln in logs), logs)
        state = self.w.load_state(self.state)
        self.assertNotIn(self._SID, state,
                         "an aborted stash must not persist an advanced nudge count")

    def test_run_once_apierror_busy_pane_pings_once_when_wedged(self):
        # #176 item 2: silence must become impossible. A genuinely BUSY pane
        # (real foreground turn, no locatable boundary at all) still NEVER gets
        # typed into, but now gets job 4's escalation shape: zero keystrokes, one
        # deduped ping per episode, once the stall runs strictly past 2x grace —
        # the incident ran 36 minutes with zero keystrokes AND zero pings.
        now = 1_000_000
        cwd = "/devel/projwedged"
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 1000, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", default_capture=_BUSY_PANE)
        logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               grace=300, interval=300, max_nudges=3)
        self.assertEqual(fake.continues_sent(), 0, "must NOT type into a running agent")
        self.assertEqual(len(self.pings), 1, "exactly one busy-pane-wedged ping")
        self.assertTrue(self.pings[0][1].startswith("apierr-busypane:"))
        self.assertTrue(any("busy-pane-wedged (api-error)" in ln for ln in logs), logs)
        self.assertTrue(any("txt=" in ln for ln in logs),
                        "the boundary classification must be logged on every skip")
        # second poll in the same episode → no second ping
        self.w.run_once(now=now + 60, run=fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state,
                        grace=300, interval=300, max_nudges=3)
        self.assertEqual(len(self.pings), 1, "one ping per wedged episode, not per poll")

    def test_run_once_apierror_busy_pane_pings_after_wall_clock_2x_grace_even_if_mtime_stays_fresh(self):
        # #176 REOPENED F2: the busy-pane ping used to be gated on transcript
        # MTIME (`idle = now - tmtime`), the one signal job 1's OWN grace
        # deliberately avoids for its other branch — CC's own retries or a
        # queue/snapshot write can keep touching the transcript while the pane
        # stays genuinely busy, holding `idle` artificially low forever. The
        # threshold must be anchored on THIS EPISODE's own first_seen (wall
        # clock) instead, so it still fires once REAL time passes 2x grace
        # even though `idle` itself never exceeds 100s here.
        now = 1_000_000
        cwd = "/devel/projwedgedfresh"
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", default_capture=_BUSY_PANE)
        grace = 300
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 100, now)
        self.w.run_once(now=now, run=fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state,
                        grace=grace, interval=300, max_nudges=3)
        self.assertEqual(self.pings, [], "must not ping before 2x grace of wall clock")
        for i in range(1, 8):
            t = now + i * 100
            self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 100, t)
            self.w.run_once(now=t, run=fake, send_fn=self._send,
                            projects_dir=self.projects, state_path=self.state,
                            grace=grace, interval=300, max_nudges=3)
        self.assertEqual(len(self.pings), 1,
                         "a genuinely busy pane must ping once past 2x grace of REAL "
                         "wall-clock time, even if something keeps the transcript's "
                         "own mtime fresh")
        self.assertTrue(self.pings[0][1].startswith("apierr-busypane:"))

    def test_run_once_apierror_stash_never_follows_a_job10_submit_in_the_same_sweep(self):
        # #176 REOPENED F3: job 1's draft-pending path used the STALE
        # top-of-sweep capture to decide whether to stash-deliver — but job 10
        # (prompt_wedge_check) runs EARLIER in the SAME sweep and can submit a
        # recognized MACHINE draft (Escape+Enter) into the exact same pane.
        # Job 1 must re-verify against a FRESH capture right before sending its
        # own C-s, or it types into a turn that started microseconds earlier
        # (job 20's own documented hazard).
        now = 1_000_000
        cwd = "/devel/projrace"
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 600, now)
        machine_text = "Priorita: prio:bounce test message"
        draft_cap = "❯\xa0" + machine_text + "\n"
        sent = []

        def run(argv, timeout=8):
            j = " ".join(argv)
            if argv[:2] == ["tmux", "list-panes"]:
                return "%5\tclaude\t" + cwd + "\n"
            if argv[:2] == ["tmux", "display-message"]:
                if "pane_in_mode" in j:
                    return "0"
                return ""
            if argv[:2] == ["tmux", "capture-pane"]:
                if any(a and a[-1] == "Enter" for a in sent):
                    return _BUSY_PANE
                return draft_cap
            if argv[:2] == ["tmux", "send-keys"]:
                sent.append(argv)
                return ""
            return ""

        import hashlib
        h = hashlib.sha1(machine_text.encode("utf-8")).hexdigest()[:12]
        state = {"pwedge:%5": {"hash": h, "n": self.w.PWEDGE_SWEEPS - 1, "pinged": False}}
        self.w.save_state(self.state, state)

        logs = self.w.run_once(now=now, run=run, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               grace=300, interval=300, max_nudges=3)
        self.assertTrue(any(a and a[-1] == "Enter" for a in sent), sent)   # job 10 submitted
        self.assertFalse(any(a and a[-1] == "C-s" for a in sent),
                         "job1 must not stash-deliver into a pane job10 just "
                         "submitted in the same sweep: %r" % sent)
        self.assertTrue(any("skip raced" in ln for ln in logs), logs)

    def test_run_once_apierror_raced_skip_pings_once_when_wedged(self):
        # #176 R2: F3's own "skip raced" branch (job 1 re-verifying against a
        # FRESH capture immediately before delivery) used to log and
        # `continue` bare on a mismatch — no state, no ping, no counter, no
        # bound — structurally the identical silent-unbounded-skip shape
        # this whole ticket removed from the busy and stash-abort branches.
        # It must now share the EXISTING `apierr-stashabort:` escalation:
        # zero keystrokes, exactly one deduped ping per episode once the
        # stall runs strictly past 2x grace of wall clock. Here the pane
        # moves between the top-of-sweep capture (draft) and job 1's own
        # fresh re-check (busy) for some OTHER reason than job 10 (which
        # never sends a keystroke in this test at all).
        now = 1_000_000
        cwd = "/devel/projraced2"
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 1000, now)
        calls = {"n": 0}

        def run(argv, timeout=8):
            if argv[:2] == ["tmux", "list-panes"]:
                return "%5\tclaude\t" + cwd + "\n"
            if argv[:2] == ["tmux", "display-message"]:
                return "0" if "pane_in_mode" in " ".join(argv) else ""
            if argv[:2] == ["tmux", "capture-pane"]:
                calls["n"] += 1
                # capture #1 = the sweep's top-of-sweep read (idle-with-draft);
                # every later capture = job 1's own fresh re-verification,
                # which no longer agrees (busy) — the "raced" shape.
                return self._DRAFT_PANE if calls["n"] == 1 else _BUSY_PANE
            return ""

        logs = self.w.run_once(now=now, run=run, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               grace=300, interval=300, max_nudges=3)
        self.assertTrue(any("skip raced" in ln for ln in logs), logs)
        self.assertEqual(len(self.pings), 1, "exactly one raced-skip-wedged ping")
        self.assertTrue(self.pings[0][1].startswith("apierr-stashabort:"))
        self.assertTrue(any("stash-abort-wedged (api-error)" in ln for ln in logs), logs)
        # second poll in the same episode, still racing → no second ping
        calls["n"] = 0
        self.w.run_once(now=now + 60, run=run, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state,
                        grace=300, interval=300, max_nudges=3)
        self.assertEqual(len(self.pings), 1, "one ping per wedged episode, not per poll")

    def test_run_once_apierror_delivers_when_the_fresh_recapture_reads_bare(self):
        # #189: job 1's fresh-capture race check asked the SAME unanswerable
        # question a second time — `if fkind != "input" or not ftxt` — so a
        # pane whose apparent content was gone by the time we were about to
        # act got skipped as "raced". But a BARE input line is perfectly
        # deliverable: the boundary BEING an input line is the whole safety
        # question, and whether it happens to look empty is not. With grey
        # autocomplete in play the top-of-sweep "draft" was frequently never
        # text at all, so this branch converted a deliverable pane into a
        # permanent skip. Here capture #1 (top of sweep) shows content and
        # every later capture shows a bare box.
        now = 1_000_000
        cwd = "/devel/projghostvanished"
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 1000, now)
        calls, box, sent = {"n": 0}, {}, []

        def run(argv, timeout=8):
            j = " ".join(argv)
            if argv[:2] == ["tmux", "list-panes"]:
                return "%5\tclaude\t" + cwd + "\n"
            if argv[:2] == ["tmux", "display-message"]:
                return "0" if "pane_in_mode" in j else ""
            if argv[:2] == ["tmux", "capture-pane"]:
                calls["n"] += 1
                if calls["n"] == 1:
                    return self._DRAFT_PANE
                if box.get("typed") and not box.get("submitted"):
                    return "❯\xa0" + box["typed"] + "\n"
                return "❯\xa0\n"
            if argv[:2] == ["tmux", "send-keys"]:
                sent.append(argv)
                if "-l" in argv:
                    box["typed"] = argv[-1]
                elif argv[-1] == "Enter":
                    box["submitted"] = True
                return ""
            return ""

        logs = self.w.run_once(now=now, run=run, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               grace=300, interval=300, max_nudges=3)
        self.assertFalse(any("skip raced" in ln for ln in logs), logs)
        self.assertEqual(box.get("typed"), self.w.NUDGE_TEXT,
                         "a bare input line must be delivered into: %r" % sent)
        self.assertTrue(box.get("submitted"), sent)
        self.assertTrue(any("(stash)" in ln for ln in logs), logs)

    def test_run_once_apierror_stashabort_pings_after_wall_clock_2x_grace_even_if_mtime_stays_fresh(self):
        # #176 R4: a surviving mutant — reverting the stash-abort branch's
        # own threshold from `(now - sb["first_seen"]) > 2 * grace` back to
        # live `idle` (transcript mtime) is literally the F2 defect this
        # ticket already fixed once, one branch over, and nothing pinned it.
        # Same anchor requirement as the busy branch: a genuinely aborted
        # stash must still ping once REAL wall-clock time passes 2x grace,
        # even if something keeps re-touching the transcript so `idle`
        # itself never crosses the threshold.
        now = 1_000_000
        cwd = "/devel/projstashabortfresh"
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         default_capture=self._DRAFT_PANE)
        grace = 300
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 100, now)
        with m.patch.object(self.w, "deliver_with_stash", return_value=False):
            self.w.run_once(now=now, run=fake, send_fn=self._send,
                            projects_dir=self.projects, state_path=self.state,
                            grace=grace, interval=300, max_nudges=3)
        self.assertEqual(self.pings, [], "must not ping before 2x grace of wall clock")
        # An aborted stash never persists state[key] (the "must not burn a
        # retry" invariant), so `decide()` re-derives "nudge is due" fresh
        # from the SAME frozen first_seen every sweep once idle >= grace —
        # 8 sweeps of 100s is what carries (now - skey.first_seen) past
        # 2*grace=600 (nudge first reaches the stash-abort branch at i=2,
        # ping fires once the WALL CLOCK since then exceeds 600 at i=8).
        for i in range(1, 9):
            t = now + i * 100
            self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 100, t)
            with m.patch.object(self.w, "deliver_with_stash", return_value=False):
                self.w.run_once(now=t, run=fake, send_fn=self._send,
                                projects_dir=self.projects, state_path=self.state,
                                grace=grace, interval=300, max_nudges=3)
        self.assertEqual(len(self.pings), 1,
                         "a genuinely aborted stash must ping once past 2x grace of "
                         "REAL wall-clock time, even if something keeps the "
                         "transcript's own mtime fresh")
        self.assertTrue(self.pings[0][1].startswith("apierr-stashabort:"))

    def test_run_once_apierror_aborted_stash_pings_once_when_wedged(self):
        # #176 REOPENED F1: the shipped fix RELOCATED the silent unbounded skip
        # from the busy branch to the stash-abort branch instead of eliminating
        # it — an occupied stash slot / a live-turn abort left this branch with
        # no state, no ping, no bound. Must get the SAME bounded escalation the
        # busy branch already has: zero keystrokes, exactly one deduped ping
        # once the episode runs strictly past 2x grace of wall clock.
        now = 1_000_000
        cwd = "/devel/projstashwedged"
        self._transcript(cwd, [{"type": "user", "message": {}}, self._ERR], 1000, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         default_capture=self._DRAFT_PANE)
        with m.patch.object(self.w, "deliver_with_stash", return_value=False):
            logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                                   projects_dir=self.projects, state_path=self.state,
                                   grace=300, interval=300, max_nudges=3)
        self.assertEqual(fake.continues_sent(), 0, "must NOT type into the drafted pane")
        self.assertEqual(len(self.pings), 1, "exactly one stash-abort-wedged ping")
        self.assertTrue(self.pings[0][1].startswith("apierr-stashabort:"))
        self.assertTrue(any("stash-abort-wedged (api-error)" in ln for ln in logs), logs)
        # second poll in the same episode, still aborting → no second ping
        with m.patch.object(self.w, "deliver_with_stash", return_value=False):
            self.w.run_once(now=now + 60, run=fake, send_fn=self._send,
                            projects_dir=self.projects, state_path=self.state,
                            grace=300, interval=300, max_nudges=3)
        self.assertEqual(len(self.pings), 1, "one ping per wedged episode, not per poll")

    def test_run_once_apierr_episode_state_is_pruned_after_wait_clear(self):
        # #176 REOPENED F7: the cleanup OR-chain must actually name BOTH the
        # busy-pane and stash-abort episode prefixes, or their state keys leak
        # forever once the session recovers — removing either branch left
        # every pre-existing test green, since neither key is a bare UUID (the
        # OTHER cleanup branch's own shape) and nothing else ever reads them.
        now = 1_000_000
        state = {
            "apierr-busypane:oldsid": {"first_seen": now - 10000,
                                       "last_seen": now - 1000, "pinged": True},
            "apierr-stashabort:oldsid2": {"first_seen": now - 10000,
                                         "last_seen": now - 1000, "pinged": True},
        }
        self.w.save_state(self.state, state)
        fake = _FakeTmux(panes="")   # no live panes at all — nothing to act on
        self.w.run_once(now=now, run=fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state,
                        grace=300, interval=300, max_nudges=3, wait_clear=90)
        st = self.w.load_state(self.state)
        self.assertNotIn("apierr-busypane:oldsid", st,
                         "a stale busy-pane episode key must be pruned, not leak forever")
        self.assertNotIn("apierr-stashabort:oldsid2", st,
                         "a stale stash-abort episode key must be pruned, not leak forever")

    def test_run_once_gated_busy_sweep_preserves_the_nudge_schedule(self):
        # #175 F1: job 1's safety gates used to `continue` BEFORE
        # `stalled.add(key)` — so a single sweep where the pane merely LOOKS
        # busy (e.g. right after our own `continue` landed) skipped the add,
        # and the cleanup pass at the end of run_once then deleted the
        # accumulated nudge/`escalated` entry (any bare-UUID key not in
        # `stalled`). The very next sweep restarted the whole #175 widening
        # schedule at nudge #1 instead of continuing it, and — separately —
        # could re-arm the one-shot "gave up" ping under a fresh dedup key.
        # This drives the REAL run_once through exactly that gated sweep and
        # asserts the schedule survives intact, with the escalation ping
        # staying single across it.
        now = 1_000_000
        cwd = "/devel/gatedbusy"
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, interval=300, max_nudges=3)

        def _stale(t):
            self._transcript(cwd, [self._ERR], 600, t)

        _stale(now)
        self.w.run_once(now=now, **kw)                      # nudge #1
        _stale(now + 300)
        self.w.run_once(now=now + 300, **kw)                 # nudge #2
        _stale(now + 600)
        self.w.run_once(now=now + 600, **kw)                 # nudge #3
        state = self.w.load_state(self.state)
        self.assertEqual(len(state[self._SID]["nudges"]), 3)

        # ONE gated sweep: the pane looks busy — job 1 must skip typing, but
        # the episode must survive the cleanup pass of THIS SAME run_once call.
        busy_fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                              default_capture=_BUSY_PANE)
        _stale(now + 900)
        self.w.run_once(now=now + 900, run=busy_fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state,
                        grace=300, interval=300, max_nudges=3)
        state = self.w.load_state(self.state)
        self.assertIn(self._SID, state,
                      "a gated (busy-pane) sweep must not delete the "
                      "accumulated nudge/escalation state")
        self.assertEqual(len(state[self._SID]["nudges"]), 3,
                         "the busy sweep itself must not add a phantom nudge")

        # Idle again → nudge #4 must fire (widened interval), CONTINUING the
        # schedule — never restarting at nudge #1 — and sets the one-shot
        # escalation flag.
        _stale(now + 1200)
        self.w.run_once(now=now + 1200, **kw)
        state = self.w.load_state(self.state)
        self.assertEqual(len(state[self._SID]["nudges"]), 4,
                         "nudge #4 must continue the preserved schedule, not "
                         "restart at #1")
        self.assertTrue(state[self._SID]["escalated"])
        escalate_pings = [p for p in self.pings if "pretrváva" in p[0]]
        self.assertEqual(len(escalate_pings), 1, "exactly one give-up ping so far")

        # A SECOND gated sweep, this time AFTER escalation — must not wipe
        # state or re-arm the one-shot escalation ping.
        busy_fake2 = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                               default_capture=_BUSY_PANE)
        _stale(now + 1500)
        self.w.run_once(now=now + 1500, run=busy_fake2, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state,
                        grace=300, interval=300, max_nudges=3)
        state = self.w.load_state(self.state)
        self.assertIn(self._SID, state,
                      "a second gated sweep after escalation must not wipe state")
        self.assertTrue(state[self._SID]["escalated"],
                        "escalated flag must survive the gated sweep")

        # Idle again → nudge #5 continues the SAME schedule; the one-shot
        # escalation ping must NOT re-fire a second time.
        _stale(now + 2700)
        self.w.run_once(now=now + 2700, **kw)
        state = self.w.load_state(self.state)
        self.assertEqual(len(state[self._SID]["nudges"]), 5,
                         "nudge #5 must continue the schedule across two "
                         "gated sweeps")
        escalate_pings = [p for p in self.pings if "pretrváva" in p[0]]
        self.assertEqual(len(escalate_pings), 1,
                         "the one-shot escalation ping must stay single "
                         "across a gated sweep, never re-arm just because "
                         "the episode key was briefly untouched")

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

    # --- job 4: ⏳ WORKING-stall self-check NUDGE (subagent-gated, escalate-on-wedge) -
    _WORKING = {"type": "assistant", "isApiErrorMessage": False,
                "message": {"role": "assistant", "content": [{"type": "text",
                            "text": "Spustil som verdict proces.\n\n⏳ WORKING: dekódujem strih"}]}}
    _WORKING_URL = {"type": "assistant", "isApiErrorMessage": False,
                    "message": {"role": "assistant", "content": [{"type": "text",
                                "text": "Beží build.\n\n⏳ WORKING: build beží\nhttp://dev/x"}]}}
    _DONE = {"type": "assistant", "isApiErrorMessage": False,
             "message": {"role": "assistant", "content": [{"type": "text",
                         "text": "Nasadené.\n\n✅ DONE: v1.2.3 nasadené"}]}}
    _QUESTION = {"type": "assistant", "isApiErrorMessage": False,
                 "message": {"role": "assistant", "content": [{"type": "text",
                             "text": "Treba rozhodnúť.\n\n❓ NEEDS YOU: 0 dB alebo preset?"}]}}
    _QUOTES_MARKER = {"type": "assistant", "isApiErrorMessage": False,
                      "message": {"role": "assistant", "content": [{"type": "text",
                                  "text": "Vysvetlenie: marker ⏳ znamená že niečo beží."}]}}

    def _subagent_transcript(self, cwd, age_s, now):
        # write a subagent transcript at <enc>/<SID>/subagents/agent-x.jsonl
        d = self.projects / self.w.encode_project_dir(cwd) / self._SID / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "agent-x.jsonl"
        p.write_text('{"type":"assistant"}\n')
        os.utime(p, (now - age_s, now - age_s))
        return p

    def test_transcript_last_marker_anchored(self):
        # marker at line start → detected (tolerating a trailing URL line)
        self.assertEqual(self.w.transcript_last_marker(
            self._transcript("/x/w", [self._WORKING], 1, 1_000_000)), "⏳")
        self.assertEqual(self.w.transcript_last_marker(
            self._transcript("/x/u", [self._WORKING_URL], 1, 1_000_000)), "⏳")
        self.assertEqual(self.w.transcript_last_marker(
            self._transcript("/x/d", [self._DONE], 1, 1_000_000)), "✅")
        self.assertEqual(self.w.transcript_last_marker(
            self._transcript("/x/q", [self._QUESTION], 1, 1_000_000)), "❓")
        self.assertEqual(self.w.transcript_last_marker(
            self._transcript("/x/n", [self._OK], 1, 1_000_000)), "")
        # a ⏳ QUOTED mid-prose is NOT a status marker (anchored match)
        self.assertEqual(self.w.transcript_last_marker(
            self._transcript("/x/p", [self._QUOTES_MARKER], 1, 1_000_000)), "")
        # an api-error entry is NOT a marker (job 1's domain)
        self.assertEqual(self.w.transcript_last_marker(
            self._transcript("/x/e", [self._ERR], 1, 1_000_000)), "")

    def _run4(self, now, fake, **kw):
        return self.w.run_once(now=now, run=fake, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               grace=300, interval=300, max_nudges=3,
                               stall_working=300, **kw)

    def test_run_once_working_stall_nudges_self_check(self):
        # ⏳ WORKING + idle >= stall_working + no subagent → ONE `stuck-check` nudge,
        # NO `continue`, and ZERO Discord pings (a landed nudge self-resolves quietly —
        # the whole point: un-stick the session without bothering the offline user).
        now, cwd = 1_000_000, "/devel/wstall"
        self._transcript(cwd, [self._WORKING], 300, now)   # idle 5 min on ⏳
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        logs = self._run4(now, fake)
        self.assertEqual(fake.selfchecks_sent(), 1, "exactly one stuck-check nudge")
        self.assertEqual(fake.continues_sent(), 0, "job 4 sends stuck-check, NOT `continue`")
        self.assertEqual(self.pings, [], "a first nudge must NOT ping (no Discord noise)")
        self.assertTrue(any("working-nudge#1" in ln for ln in logs))

    def test_run_once_working_stall_skipped_when_pane_busy(self):
        # THE #233 INCIDENT: ⏳ WORKING + idle transcript (a FOREGROUND agent blocks the
        # parent, freezing its transcript) but the PANE is running the agent (spinner, no
        # free `❯`). A nudge keystroke would INTERRUPT the live agent → must skip
        # busy-pane, send NOTHING. Idle here is below 2× threshold → NOT yet a wedge ping.
        now, cwd = 1_000_000, "/devel/wbusy"
        self._transcript(cwd, [self._WORKING], 450, now)   # >300 (enters) but <600 (no ping)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", default_capture=_BUSY_PANE)
        logs = self._run4(now, fake)
        self.assertEqual(fake.selfchecks_sent(), 0, "MUST NOT type into a busy pane")
        self.assertEqual(fake.continues_sent(), 0)
        self.assertEqual(self.pings, [], "below 2× threshold → not yet a wedge ping")
        self.assertTrue(any("skip busy-pane (working-stall)" in ln for ln in logs))

    def test_run_once_busy_pane_wedged_pings_only(self):
        # #3: a busy pane (foreground agent, no free `❯`) with NO advancing subagent that
        # stays stuck a LONG time (≥ 2× stall_working) is a genuinely wedged/hung turn.
        # A ping never interrupts → escalate to ONE ping, NEVER a keystroke; one/episode.
        now, cwd = 1_000_000, "/devel/wwedge"
        self._transcript(cwd, [self._WORKING], 3600, now)   # idle 1h ≥ 2×300
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", default_capture=_BUSY_PANE)
        logs = self._run4(now, fake)
        self.assertEqual(fake.selfchecks_sent() + fake.continues_sent(), 0,
                         "wedged busy pane must NEVER be typed into")
        self.assertEqual(len(self.pings), 1, "exactly one busy-pane-wedged ping")
        self.assertIn("wwedge", self.pings[0][0])
        self.assertTrue(self.pings[0][1].startswith("busypane:"))
        self.assertTrue(any("busy-pane-wedged" in ln for ln in logs))
        # second poll in the same episode → no second ping
        self._run4(now + 60, fake)
        self.assertEqual(len(self.pings), 1, "one ping per wedged episode, not per poll")

    def test_run_once_working_stall_skipped_when_subagent_active(self):
        # a live SUBAGENT transcript → the parent ⏳ is HEALTHY waiting → NO nudge
        now, cwd = 1_000_000, "/devel/wsub"
        self._transcript(cwd, [self._WORKING], 3600, now)  # parent idle 1h on ⏳
        self._subagent_transcript(cwd, 10, now)            # subagent advanced 10s ago
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        self.assertEqual(self.pings, [], "live subagent → not a stall")
        self.assertEqual(fake.continues_sent() + fake.selfchecks_sent(), 0)

    def test_run_once_working_stall_nudges_when_subagent_stale(self):
        # a subagent dir exists but its transcript is OLD (beyond the window) → the
        # subagent finished/died long ago; the parent is genuinely idle → nudge
        now, cwd = 1_000_000, "/devel/wsubold"
        self._transcript(cwd, [self._WORKING], 600, now)
        self._subagent_transcript(cwd, 5000, now)          # last subagent write 83 min ago
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        self.assertEqual(fake.selfchecks_sent(), 1)
        self.assertEqual(self.pings, [])

    def test_run_once_no_working_nudge_below_threshold(self):
        # ⏳ but idle < stall_working → probably fine, no nudge yet
        now, cwd = 1_000_000, "/devel/wfresh"
        self._transcript(cwd, [self._WORKING], 120, now)   # only 2 min idle
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        self.assertEqual(fake.selfchecks_sent(), 0)
        self.assertEqual(self.pings, [])

    def test_run_once_no_working_nudge_when_done(self):
        # ✅ DONE idle long = correctly idle awaiting the user — never nudged as stall
        now, cwd = 1_000_000, "/devel/wdone"
        self._transcript(cwd, [self._DONE], 3600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        self.assertEqual(fake.selfchecks_sent(), 0)
        self.assertEqual(self.pings, [])

    def test_run_once_no_working_nudge_when_question(self):
        # ❓ marker → waiting on the user (job 2's domain), not a working-stall
        now, cwd = 1_000_000, "/devel/wq"
        self._transcript(cwd, [self._QUESTION], 3600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        self.assertEqual(fake.selfchecks_sent(), 0)
        self.assertEqual(self.pings, [])

    def test_run_once_working_stall_nudges_once_per_episode(self):
        # the SAME still-stuck ⏳ episode nudges exactly once within the retry interval
        # (repeated polls before `working_interval` elapses → no re-nudge)
        now, cwd = 1_000_000, "/devel/wonce"
        self._transcript(cwd, [self._WORKING], 300, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake, working_interval=300)
        self._run4(now + 60, fake, working_interval=300)
        self._run4(now + 120, fake, working_interval=300)
        self.assertEqual(fake.selfchecks_sent(), 1, "one nudge per retry interval")
        self.assertEqual(self.pings, [])

    def test_run_once_working_stall_self_resolves_after_nudge(self):
        # a LANDED nudge: the session reacts, its transcript goes fresh (idle resets)
        # while still ⏳ (it re-checked and keeps working) → no second nudge, no ping.
        now, cwd = 1_000_000, "/devel/wresolve"
        self._transcript(cwd, [self._WORKING], 300, now)        # idle 5 min → nudge#1
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake, working_interval=300)
        self.assertEqual(fake.selfchecks_sent(), 1)
        self._transcript(cwd, [self._WORKING], 5, now + 60)     # responded → fresh, still ⏳
        self._run4(now + 60, fake, working_interval=300)
        self.assertEqual(fake.selfchecks_sent(), 1, "responded → no re-nudge")
        self.assertEqual(self.pings, [], "self-resolved → never ping")

    def test_run_once_working_stall_escalates_when_wedged(self):
        # the Claude process itself is wedged: the keystroke produces no response, idle
        # keeps growing → 3 nudges spaced by the interval, then ONE give-up ping.
        now, cwd = 1_000_000, "/devel/wwedged"
        self._transcript(cwd, [self._WORKING], 300, now)        # never rewritten → idle grows
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake, working_interval=300)             # nudge#1
        self._run4(now + 300, fake, working_interval=300)       # nudge#2
        self._run4(now + 600, fake, working_interval=300)       # nudge#3
        self._run4(now + 900, fake, working_interval=300)       # escalate (give-up ping)
        self._run4(now + 1200, fake, working_interval=300)      # noop
        self.assertEqual(fake.selfchecks_sent(), 3, "exactly 3 nudges then stop")
        self.assertEqual(len(self.pings), 1, "one give-up ping after MAX nudges")
        self.assertIn("nereaguje", self.pings[0][0])

    def test_run_once_working_stall_skips_pane_in_copy_mode(self):
        # the user is scrolling the pane (copy-mode) → never inject keys, no state burn
        now, cwd = 1_000_000, "/devel/wcopy"
        self._transcript(cwd, [self._WORKING], 300, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", modes={"%5": "1"})
        self._run4(now, fake, working_interval=300)
        self.assertEqual(fake.selfchecks_sent(), 0, "must NOT type into a scrolled pane")
        self.assertEqual(self.pings, [])

    def test_run_once_apierror_precedes_working(self):
        # an api-error stall is job 1's (a `continue`), NOT a job-4 working stuck-check
        now, cwd = 1_000_000, "/devel/wboth"
        self._transcript(cwd, [self._WORKING, self._ERR], 600, now)  # last entry = error
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        self.assertEqual(fake.continues_sent(), 1, "api-error → continue")
        self.assertEqual(fake.selfchecks_sent(), 0, "not a working-stall nudge")

    # --- (#352) live-shell-evidence pane classifier --------------------------
    def test_pane_live_shell_evidence_singular(self):
        cap = ("● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n"
               "  ⏵⏵ bypass permissions on · 1 shell\n")
        self.assertTrue(self.w._pane_live_shell_evidence(cap))

    def test_pane_live_shell_evidence_plural(self):
        cap = ("● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n"
               "  ⏵⏵ bypass permissions on · 3 shells\n")
        self.assertTrue(self.w._pane_live_shell_evidence(cap))

    def test_pane_live_shell_evidence_monitor_form(self):
        # CC also badges its "monitor" kind of local_bash task the same way
        cap = ("● Hotovo.\n❯ \n  ⏵⏵ bypass permissions on · 1 monitor\n")
        self.assertTrue(self.w._pane_live_shell_evidence(cap))

    def test_pane_live_shell_evidence_false_on_plain_idle_pane(self):
        # the ordinary idle footer (no bg task) carries no count at all
        self.assertFalse(self.w._pane_live_shell_evidence(_IDLE_PANE))

    def test_pane_live_shell_evidence_ignores_prose_mention_elsewhere(self):
        # "3 shells" appearing OUTSIDE the ⏵⏵ mode-hint line (e.g. quoted in a
        # completion report or this very playbook) must NEVER be mistaken for
        # live evidence — the same chrome-line discipline every other footer
        # reader in this file already applies (mention vs use).
        cap = ("● Cleaned up 3 shells from the leftover pool.\n❯ \n"
               "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")
        self.assertFalse(self.w._pane_live_shell_evidence(cap))

    def test_pane_live_shell_evidence_empty_capture(self):
        self.assertFalse(self.w._pane_live_shell_evidence(""))
        self.assertFalse(self.w._pane_live_shell_evidence(None))

    def test_pane_live_shell_evidence_ignores_a_quoted_badge_line_in_scrollback(self):
        # (#352 F1, adversarial review round 1) A capture can contain an
        # OLDER quoted rendering of the ⏵⏵ badge (a completion report
        # discussing this exact ticket, a playbook excerpt) sitting ABOVE
        # the real conversation and the pane's OWN current (badge-free)
        # footer. Scanning the WHOLE capture for any `⏵⏵`-prefixed line
        # would misread that scrollback quote as live evidence; only a
        # `⏵⏵` line reached by walking the pane's genuinely TRAILING
        # chrome (bottom-up, stopping at the first non-chrome row — the
        # bare `❯` input-box boundary here) may count.
        cap = ("Poznamka z minuleho debugovania: bol tam presne tento riadok:\n"
               "  ⏵⏵ bypass permissions on · 1 shell\n"
               "ale to uz davno skoncilo.\n"
               "❯ \n"
               "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")
        self.assertFalse(self.w._pane_live_shell_evidence(cap),
                         "a scrollback-quoted badge line must never count as CURRENT evidence")

    # --- (#352) job 4 skips the working-stall check entirely while the pane
    # itself already proves the session alive (a live bg shell/monitor badge)
    _LIVE_SHELL_PANE = ("● Waiting for 1 background agent to finish\n❯ \n"
                        "  ctx ███░  caveman:lite\n"
                        "  ⏵⏵ bypass permissions on · 1 shell\n")

    def test_run_once_working_stall_skips_when_live_shell_evidence(self):
        now, cwd = 1_000_000, "/devel/wliveshell"
        self._transcript(cwd, [self._WORKING], 3600, now)   # idle 1h, well past threshold
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         default_capture=self._LIVE_SHELL_PANE)
        logs = self._run4(now, fake)
        self.assertEqual(fake.selfchecks_sent(), 0,
                         "pane-visible live shell → never burn a self-check nudge")
        self.assertEqual(fake.continues_sent(), 0)
        self.assertEqual(self.pings, [], "must not ping either — evidence already proves life")
        self.assertTrue(any("skip live-shell (working-stall)" in ln for ln in logs), logs)

    def test_run_once_working_stall_skip_also_covers_busy_pane_branch(self):
        # the busy-pane (no free ❯) escalate-ping path must ALSO be skipped —
        # "before ANY nudge", not just the keystroke branch.
        now, cwd = 1_000_000, "/devel/wliveshellbusy"
        self._transcript(cwd, [self._WORKING], 3600, now)   # ≥ 2× threshold too
        busy_with_shell = ("● Validate issue #233\n  ⎿ running…\n"
                           "✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
                           "  ⏵⏵ bypass permissions on · 2 shells\n")
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", default_capture=busy_with_shell)
        self._run4(now, fake)
        self.assertEqual(self.pings, [], "busy-pane wedge ping must also be skipped")
        self.assertEqual(fake.selfchecks_sent() + fake.continues_sent(), 0)

    def test_run_once_working_stall_resumes_once_shell_evidence_gone(self):
        # (#352 item 3) the exit condition is REACHABLE: a shell that later
        # dies (badge gone from the footer) must re-enable normal nudging on
        # the very next sweep — never a permanent skip.
        now, cwd = 1_000_000, "/devel/wliveshellgone"
        self._transcript(cwd, [self._WORKING], 3600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         default_capture=self._LIVE_SHELL_PANE)
        self._run4(now, fake)
        self.assertEqual(fake.selfchecks_sent(), 0, "skipped while evidence lives")
        # the shell died — footer reverts to the plain idle prompt
        fake.default_capture = _IDLE_PANE
        self._run4(now + 60, fake)
        self.assertEqual(fake.selfchecks_sent(), 1,
                         "evidence gone → normal working-stall nudge resumes")

    def test_run_once_live_shell_skip_preserves_episode_state_across_wait_clear(self):
        # An ALREADY-nudged episode's history (nudges/answered) must survive a
        # long stretch of live-shell skipping that spans MORE than wait_clear
        # (90s) — the end-of-sweep cleanup prunes an episode key once its
        # last_seen goes stale that long, which would otherwise silently wipe
        # the escalation history mid-skip (the skip branch never calls
        # decide_working, so last_seen must be refreshed by hand).
        now, cwd = 1_000_000, "/devel/wliveshellpersist"
        self._transcript(cwd, [self._WORKING], 3600, now)
        wkey = "working:" + self._SID
        seeded_nudges = [now - 1000]
        self.w.save_state(self.state, {wkey: {"first_seen": now - 2000,
                                              "nudges": list(seeded_nudges),
                                              "answered": 1, "escalated": False,
                                              "last_seen": now - 1000}})
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         default_capture=self._LIVE_SHELL_PANE)
        self._run4(now, fake)
        self._run4(now + 200, fake)          # > wait_clear (90s) since the seed's last_seen
        st = self.w.load_state(self.state)
        self.assertIn(wkey, st, "episode must survive the skip, not get pruned as stale")
        self.assertEqual(st[wkey]["nudges"], seeded_nudges,
                         "nudge/answered history must stay FROZEN during the skip, "
                         "not reset to a fresh first-sighting episode")
        self.assertEqual(fake.selfchecks_sent(), 0)

    def test_run_once_live_shell_skip_also_preserves_busypane_seed(self):
        # (#352 F4, adversarial review round 1) the SAME persistence must
        # hold for a seeded `busypane:` entry, not just `working:` — a
        # mutant dropping just that half of the refresh loop must be
        # caught, not merely one that drops both.
        now, cwd = 1_000_000, "/devel/wliveshellbusypersist"
        self._transcript(cwd, [self._WORKING], 3600, now)
        bkey = "busypane:" + self._SID
        self.w.save_state(self.state, {bkey: {"first_seen": now - 2000,
                                              "pinged": True, "last_seen": now - 1000}})
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         default_capture=self._LIVE_SHELL_PANE)
        self._run4(now, fake)
        self._run4(now + 200, fake)
        st = self.w.load_state(self.state)
        self.assertIn(bkey, st, "seeded busypane: entry must ALSO survive the skip")
        self.assertTrue(st[bkey]["pinged"], "its own history must stay untouched, not reset")

    def test_run_once_responded_history_survives_the_regrowth_gap(self):
        # (#352 F2, adversarial review round 1) After a landed nudge is
        # ANSWERED, idle drops near zero and the OUTER job-4 gate
        # (idle >= stall_working) goes FALSE for the whole regrowth window
        # -- during which NOTHING in job 4 touches this session's state at
        # all (neither the live-shell skip above nor decide_working runs).
        # The generic wait_clear (90s) cleanup must NOT delete a `working:`
        # entry that carries real ANSWERED history during this gap, or the
        # whole 1h/3h/6h schedule silently resets to a fresh nudge#1 every
        # single time and is never actually reachable.
        now, cwd = 1_000_000, "/devel/wresponded30"
        # a FRESH transcript write (idle=100s, well under stall_working=300
        # that _run4 uses) — the OUTER job-4 gate is FALSE this sweep
        self._transcript(cwd, [self._WORKING], 100, now)
        wkey = "working:" + self._SID
        stale_last_seen = now - 200   # > the OLD 90s TTL, < the NEW 2x-stall_working one
        self.w.save_state(self.state, {wkey: {"first_seen": now - 5000,
                                              "nudges": [now - 5000],
                                              "answered": 1, "escalated": False,
                                              "last_seen": stale_last_seen}})
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        st = self.w.load_state(self.state)
        self.assertIn(wkey, st, "answered history must survive the regrowth gap, "
                     "not just the generic 90s window")
        self.assertEqual(st[wkey]["answered"], 1, "history must stay intact, not reset")

    def test_run_once_first_cycle_history_also_survives_the_regrowth_gap(self):
        # (#352 F2, adversarial review round 2, finding 1) INVERTS the old
        # "no answered history -> generic 90s TTL" assertion: gating the
        # carve-out on `answered` was organically UNREACHABLE -- `answered`
        # is only ever set INSIDE decide_working's `responded=True` branch,
        # which itself needs a `working:` entry to have already survived one
        # full regrowth gap (idle must regrow past stall_working before
        # decide_working runs again at all). A first-cycle entry (nudged
        # once, never yet re-checked) can NEVER acquire `answered` before
        # the OLD 90s-only carve-out already pruned it -- so the whole
        # 1h/3h/6h staged schedule was dead code for every real session; the
        # bootstrap circularity, proven live against the round-1 fix. Fix:
        # key the carve-out on `nudges` (present the instant a wedged
        # episode is nudged, first sighting or not) + `not escalated`, never
        # on `answered` specifically -- so a bare first-cycle nudge history
        # now ALSO survives the regrowth gap, not just a re-confirmed one.
        now, cwd = 1_000_000, "/devel/wnoresp"
        self._transcript(cwd, [self._WORKING], 100, now)
        wkey = "working:" + self._SID
        self.w.save_state(self.state, {wkey: {"first_seen": now - 5000,
                                              "nudges": [now - 5000],
                                              "escalated": False,
                                              "last_seen": now - 200}})
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        st = self.w.load_state(self.state)
        self.assertIn(wkey, st, "a bare first-cycle nudge history (no `answered` yet) "
                     "must ALSO survive the regrowth gap -- the carve-out keys on "
                     "`nudges`, never on `answered` alone")

    def test_run_once_escalated_history_still_uses_the_generic_90s_window(self):
        # (#352 F2, adversarial review round 2, finding 1) the `not
        # escalated` half of the carve-out predicate is load-bearing: an
        # ALREADY-escalated entry surviving past the generic 90s window
        # into a brand-new stall (same session, same wkey) would become
        # IMMORTAL under decide_working's own unconditional top-of-function
        # `last_seen` refresh (it fires even on the `escalated -> noop`
        # early return) -- and, worse, decide_working's very own escalated
        # check (`if e.get("escalated"): return "noop", e`) would silently
        # swallow the NEW episode's entire nudge ladder, since the same key
        # would never look like a fresh first sighting again. An escalated
        # entry must therefore keep pruning at the ORIGINAL generic 90s TTL
        # -- proven by hand-mutation (dropping `not escalated` here lets
        # this exact fixture survive to the 2x-stall_working TTL instead).
        now, cwd = 1_000_000, "/devel/wescalated"
        self._transcript(cwd, [self._WORKING], 100, now)
        wkey = "working:" + self._SID
        self.w.save_state(self.state, {wkey: {"first_seen": now - 5000,
                                              "nudges": [now - 5000, now - 4000,
                                                         now - 3000],
                                              "escalated": True,
                                              "last_seen": now - 200}})
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        st = self.w.load_state(self.state)
        self.assertNotIn(wkey, st, "an ESCALATED entry must still be pruned at the "
                     "generic 90s TTL, never extended -- or a later, genuinely NEW "
                     "stall on the same session would be swallowed as `noop` forever")

    def test_run_once_carveout_ttl_is_2x_stall_not_1x(self):
        # (#352 F2, adversarial review round 2, finding 4) the existing
        # regrowth-gap regression lock ages its fixture at only 200s, which
        # is BELOW both `stall_working` (300s) and `2 * stall_working`
        # (600s) in this test harness -- so it cannot discriminate a
        # correct `2 * stall_working` TTL from an accidentally-shrunk
        # `stall_working` one. Age the fixture strictly past 1x (300s) but
        # still under 2x (600s): only the genuine `2 * stall_working` TTL
        # keeps it alive.
        now, cwd = 1_000_000, "/devel/wttlmult"
        self._transcript(cwd, [self._WORKING], 100, now)
        wkey = "working:" + self._SID
        stall_working = 300
        aged_last_seen = now - (stall_working + 100)   # 400s: > 1x, < 2x
        self.w.save_state(self.state, {wkey: {"first_seen": now - 5000,
                                              "nudges": [now - 5000],
                                              "escalated": False,
                                              "last_seen": aged_last_seen}})
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        st = self.w.load_state(self.state)
        self.assertIn(wkey, st, "the carve-out TTL must be the FULL 2x-stall_working "
                     "window, not a bare 1x -- a mutation shrinking it to "
                     "`stall_working` alone must be caught here")

    def test_run_once_carveout_ttl_still_has_an_upper_bound(self):
        # (#352, adversarial review round 3, F2-sibling coverage) the
        # previous test only ever proves the LOWER edge of the 2x TTL
        # (survives past 1x, under 2x) -- nothing asserted the UPPER edge:
        # that an eligible (nudged, not-escalated) entry genuinely gets
        # pruned once it goes stale PAST `2 * stall_working`, rather than
        # the carve-out silently making it immortal. Age the fixture past
        # the FULL 2x window and require it gone.
        now, cwd = 1_000_000, "/devel/wttlupper"
        self._transcript(cwd, [self._WORKING], 100, now)
        wkey = "working:" + self._SID
        stall_working = 300
        aged_last_seen = now - (2 * stall_working + 100)   # 700s: past the full 2x TTL
        self.w.save_state(self.state, {wkey: {"first_seen": now - 5000,
                                              "nudges": [now - 5000],
                                              "escalated": False,
                                              "last_seen": aged_last_seen}})
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        self._run4(now, fake)
        st = self.w.load_state(self.state)
        self.assertNotIn(wkey, st, "an eligible entry stale past the FULL "
                     "2x-stall_working window must still be pruned -- the carve-out "
                     "extends the TTL, it does not make the entry immortal")

    def test_run_once_live_shell_trust_cap_forces_a_periodic_real_check(self):
        # (#352 F3, adversarial review round 1) A pane whose LAST RENDER
        # shows the badge but never genuinely advances (repro of a wedged
        # CC process: the badge is never refreshed because nothing is
        # rendering any more) must NOT be trusted forever -- past
        # LIVE_SHELL_TRUST_CAP_S of CONSECUTIVE skipping, the very next
        # sweep must fall through to a REAL check instead of skipping
        # again unconditionally.
        now, cwd = 1_000_000, "/devel/wliveshelltrustcap"
        self._transcript(cwd, [self._WORKING], 3600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         default_capture=self._LIVE_SHELL_PANE)
        cap = self.w.LIVE_SHELL_TRUST_CAP_S
        logs = self._run4(now, fake)
        self.assertTrue(any("skip live-shell" in ln for ln in logs))
        self.assertEqual(fake.selfchecks_sent(), 0)
        # still well within the cap → keeps skipping
        self._run4(now + cap - 1, fake)
        self.assertEqual(fake.selfchecks_sent(), 0, "must still trust the badge before the cap")
        # the cap is exceeded → the SAME still-showing badge must no longer
        # be trusted unconditionally; the normal flow gets a real look
        logs2 = self._run4(now + cap, fake)
        self.assertTrue(any("live-shell trust cap exceeded" in ln for ln in logs2), logs2)
        self.assertEqual(fake.selfchecks_sent(), 1,
                         "past the trust cap, a real nudge attempt must fire — "
                         "the skip must never be permanent")
        # (#352 F5, adversarial review round 2) the forced check must reset
        # the streak — the VERY NEXT sweep, one minute later, must resume
        # skipping (never re-force again immediately) and must NOT fire a
        # second nudge attempt, proving exactly ONE real check per
        # LIVE_SHELL_TRUST_CAP_S window rather than a permanently-broken
        # trust (which would spam a nudge every sweep from here on).
        logs3 = self._run4(now + cap + 60, fake)
        self.assertTrue(any("skip live-shell" in ln for ln in logs3), logs3)
        self.assertEqual(fake.selfchecks_sent(), 1,
                         "the skip must resume after a forced check, not stay forced — "
                         "no second nudge on the very next sweep")

    # --- decide_working state machine (job 4) --------------------------------
    def _decw(self, st, key, now, idle):
        return self.w.decide_working(st, key, now, idle, interval=300, max_nudges=3)

    def test_decide_working_lifecycle(self):
        # first sighting (already past threshold) nudges immediately; then a re-nudge
        # every interval up to MAX, then escalate once, then noop.
        st, now = {}, 1_000_000
        a, e = self._decw(st, "w", now, 3000)            # idle past threshold
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 1)
        st["w"] = e
        a, e = self._decw(st, "w", now + 100, 3100)      # within interval → hold
        self.assertEqual(a, "wait")
        st["w"] = e
        a, e = self._decw(st, "w", now + 300, 3300)      # +interval → nudge#2
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 2)
        st["w"] = e
        a, e = self._decw(st, "w", now + 600, 3600)      # nudge#3
        self.assertEqual(a, "nudge")
        self.assertEqual(len(e["nudges"]), 3)
        st["w"] = e
        a, e = self._decw(st, "w", now + 900, 3900)      # MAX → escalate once
        self.assertEqual(a, "escalate")
        self.assertTrue(e["escalated"])
        st["w"] = e
        a, e = self._decw(st, "w", now + 1200, 4200)     # then noop
        self.assertEqual(a, "noop")

    def test_decide_working_first_seen_seeded_from_idle(self):
        # first_seen counts from when the stall really began (now - idle), not now
        st, now = {}, 1_000_000
        _, e = self._decw(st, "w", now, 2700)
        self.assertEqual(e["first_seen"], now - 2700)

    # --- (#352) decide_working RESPONDED backoff — explicit 1h/3h/6h schedule
    def test_decide_working_responded_schedule_is_1h_3h_6h(self):
        # NOTE: `responded=True` advances `e["answered"]` on EVERY call, even a
        # "wait" verdict (pre-existing decide_working behaviour, unchanged by
        # this ticket) — so each "must not fire before the step" probe below
        # runs against a DISCARDED deep-copy of state, never the real
        # progression, or it would itself advance past the step it's probing.
        import copy
        self.assertEqual(self.w.WORKING_RESPONDED_BACKOFF_SCHEDULE_S,
                         (3600, 3 * 3600, 6 * 3600))
        st, now = {}, 1_000_000
        a, e = self._decw(st, "w", now, 3000)          # nudge #1 (first sighting)
        self.assertEqual(a, "nudge")
        st["w"] = e
        base = now

        # 1st post-response repeat: due at +3600s (the 1h step)
        probe = copy.deepcopy(st)
        a, _ = self.w.decide_working(probe, "w", base + 3599, 3000, interval=300,
                                     max_nudges=3, responded=True)
        self.assertEqual(a, "wait", "must not fire 1s before the 1h step")
        a, e = self.w.decide_working(st, "w", base + 3600, 3000, interval=300,
                                     max_nudges=3, responded=True)
        self.assertEqual(a, "nudge", "fires exactly at the 1h step")
        self.assertEqual(e["answered"], 1)
        st["w"] = e
        base += 3600

        # 2nd post-response repeat: due at +3h (NOT the naive-exponential 2h)
        probe = copy.deepcopy(st)
        a, _ = self.w.decide_working(probe, "w", base + 3 * 3600 - 1, 3000,
                                     interval=300, max_nudges=3, responded=True)
        self.assertEqual(a, "wait", "must not fire 1s before the 3h step")
        a, e = self.w.decide_working(st, "w", base + 3 * 3600, 3000,
                                     interval=300, max_nudges=3, responded=True)
        self.assertEqual(a, "nudge", "fires exactly at the 3h step")
        self.assertEqual(e["answered"], 2)
        st["w"] = e
        base += 3 * 3600

        # 3rd post-response repeat: due at +6h
        probe = copy.deepcopy(st)
        a, _ = self.w.decide_working(probe, "w", base + 6 * 3600 - 1, 3000,
                                     interval=300, max_nudges=3, responded=True)
        self.assertEqual(a, "wait", "must not fire 1s before the 6h step")
        a, e = self.w.decide_working(st, "w", base + 6 * 3600, 3000,
                                     interval=300, max_nudges=3, responded=True)
        self.assertEqual(a, "nudge", "fires exactly at the 6h step")
        self.assertEqual(e["answered"], 3)
        st["w"] = e
        base += 6 * 3600

        # 4th+ repeat: HOLDS at the last (6h) step — never widens further,
        # never collapses back to a shorter interval
        a, e = self.w.decide_working(st, "w", base + 6 * 3600, 3000,
                                     interval=300, max_nudges=3, responded=True)
        self.assertEqual(a, "nudge", "still fires at exactly 6h on the held step")
        self.assertEqual(e["answered"], 4)

    # --- job 5: deliver a pending ✅ (idle_prompt backstop) ------------------
    def _txn_for_sid(self, sid, entries, age_s, now, cwd="/devel/projx"):
        # write a transcript named <sid>.jsonl (so _transcript_for_sid finds it),
        # carrying a cwd field so _cwd_from_transcript resolves a project name
        d = self.projects / "enc-dir"
        d.mkdir(parents=True, exist_ok=True)
        p = d / (sid + ".jsonl")
        rows = [dict(e, cwd=cwd) if isinstance(e, dict) else e for e in entries]
        p.write_text("\n".join(json.dumps(e) for e in rows) + "\n")
        os.utime(p, (now - age_s, now - age_s))
        return p

    def _deliver(self, now, prefix, **kw):
        kw.setdefault("bg_check", lambda c: False)
        return self.w.deliver_pending_done(
            now, self._send, self.projects, dry_run=kw.pop("dry_run", False),
            done_grace=120, max_stale=3600, pending_prefix=prefix, **kw)

    def test_deliver_done_sends_when_idle_and_still_done(self):
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidA", [self._DONE], 300, now, cwd="/devel/projx")
        pf = prefix + "sidA"
        Path(pf).write_text("✅ hotová práca, čakám")
        self._deliver(now, prefix)
        self.assertEqual(len(self.pings), 1, "delivers the ✅ idle_prompt missed")
        self.assertIn("hotovo", self.pings[0][0])
        self.assertIn("projx", self.pings[0][0])
        self.assertFalse(os.path.exists(pf), "pending claimed/consumed")

    def test_deliver_done_cleared_when_refired(self):
        # the user's exact worry: a session that said ✅ then a bg task re-fired it
        # (now ⏳) must NOT be pinged "done" — clear the stale pending silently.
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidB", [self._DONE, self._WORKING], 300, now)
        pf = prefix + "sidB"
        Path(pf).write_text("✅ hotovo")
        self._deliver(now, prefix)
        self.assertEqual(self.pings, [], "re-fired session → never ping done")
        self.assertFalse(os.path.exists(pf), "stale ✅ cleared")

    def test_deliver_done_too_fresh_keeps(self):
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidC", [self._DONE], 30, now)
        pf = prefix + "sidC"
        Path(pf).write_text("✅ hotovo")
        self._deliver(now, prefix)
        self.assertEqual(self.pings, [])
        self.assertTrue(os.path.exists(pf), "too fresh → keep for next poll / idle hook")

    def test_deliver_done_stale_cleared_no_ping(self):
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidD", [self._DONE], 99999, now)   # idle > max_stale
        pf = prefix + "sidD"
        Path(pf).write_text("✅ hotovo")
        self._deliver(now, prefix)
        self.assertEqual(self.pings, [], "legacy orphan → clear, don't ping a day-old done")
        self.assertFalse(os.path.exists(pf))

    def test_deliver_done_bg_monitor_defers(self):
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidE", [self._DONE], 300, now)
        pf = prefix + "sidE"
        Path(pf).write_text("✅ hotovo")
        self._deliver(now, prefix, bg_check=lambda c: True)
        self.assertEqual(self.pings, [], "bg monitor alive → ✅ likely intermediate")
        self.assertTrue(os.path.exists(pf), "deferred, not consumed")

    def test_deliver_done_uses_session_owner(self):
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidF", [self._DONE], 300, now)
        pf = prefix + "sidF"
        Path(pf).write_text("✅ hotovo")
        self._deliver(now, prefix, owner_by_sid={"sidF": "marek"})
        self.assertEqual(self.pings[0][2], "marek", "@mentions the session's owner")

    def test_deliver_done_owner_from_cwd_when_sid_missing(self):
        # LIVE INCIDENT (dev2, 2026-07-29 17:00): the presenter ✅ was delivered
        # in a sweep whose pane loop had not registered that session, so
        # owner_by_sid missed it and the ping fell through to account_owner —
        # "the FIRST owner seen", which on that box was david (the codex-bridge
        # pane). zbynek's project reported itself into david's thread.
        # The session's own cwd identifies the owner without the sid mapping.
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidOWN", [self._DONE], 300, now, cwd="/devel/projx")
        pf = prefix + "sidOWN"
        Path(pf).write_text("✅ hotovo")
        self._deliver(now, prefix, owner_by_sid={}, account_owner="david",
                      owner_by_cwd={"/devel/projx": "zbynek"})
        self.assertEqual(self.pings[0][2], "zbynek",
                         "cwd identifies the owner — never the first-seen owner")

    def test_deliver_done_no_mention_rather_than_wrong_owner(self):
        # Multi-owner box, session unresolvable by sid AND by cwd: the
        # account_owner fallback is arbitrary there, and a ✅ landing in the
        # wrong person's thread is worse than one with no @mention — the real
        # owner never sees it and someone else gets the noise.
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidAMB", [self._DONE], 300, now, cwd="/devel/other")
        pf = prefix + "sidAMB"
        Path(pf).write_text("✅ hotovo")
        self._deliver(now, prefix, owner_by_sid={}, account_owner="david",
                      owners_seen=["david", "zbynek", "marek"])
        self.assertIsNone(self.pings[0][2],
                          "ambiguous owner → no mention, never a guessed one")

    def test_deliver_done_single_owner_box_keeps_account_fallback(self):
        # The fallback still earns its place on a one-owner box: there is
        # exactly one person it could be, so a missing sid mapping must not
        # cost the mention.
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidONE", [self._DONE], 300, now, cwd="/devel/solo")
        pf = prefix + "sidONE"
        Path(pf).write_text("✅ hotovo")
        self._deliver(now, prefix, owner_by_sid={}, account_owner="zbynek",
                      owners_seen=["zbynek"])
        self.assertEqual(self.pings[0][2], "zbynek")

    def test_deliver_done_orphan_no_transcript(self):
        # session pane closed, transcript gone → trust the recorded ✅, deliver on age
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        pf = prefix + "sidGHOST"
        Path(pf).write_text("✅ hotovo orphan")
        os.utime(pf, (now - 300, now - 300))
        self._deliver(now, prefix)
        self.assertEqual(len(self.pings), 1, "orphaned pending delivered on its own age")
        self.assertFalse(os.path.exists(pf))

    def test_deliver_done_dry_run_nondestructive(self):
        now, prefix = 1_000_000, os.path.join(self.tmp, "pend-")
        self._txn_for_sid("sidH", [self._DONE], 300, now)
        pf = prefix + "sidH"
        Path(pf).write_text("✅ hotovo")
        self._deliver(now, prefix, dry_run=True)
        self.assertTrue(os.path.exists(pf), "dry_run must NOT remove the pending")

    def test_run_once_backs_off_but_never_gives_up(self):
        # #175: past MAX_NUDGES the watchdog no longer stops sending `continue` —
        # it widens the interval and keeps going, with exactly ONE "gave up"
        # ping (never a repeat, never a permanent noop).
        now = 1_000_000
        cwd = "/devel/stuck"
        self._transcript(cwd, [self._ERR], 600, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n")
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, interval=300, max_nudges=3)
        self.w.run_once(now=now, **kw)             # nudge #1 + stall ping
        self.w.run_once(now=now + 300, **kw)       # #2
        self.w.run_once(now=now + 600, **kw)       # #3
        self.w.run_once(now=now + 900, **kw)       # not due yet (widened to 600s) → wait
        self.w.run_once(now=now + 1200, **kw)      # #4 → widening kicks in, one-shot give-up ping
        self.assertEqual(fake.continues_sent(), 4, "backing off, never stopping — #4 still sent")
        self.w.run_once(now=now + 2400, **kw)      # #5 (+1200, widened again)
        self.assertEqual(fake.continues_sent(), 5, "keeps nudging well past the old give-up point")
        # exactly 2 pings total: first-nudge stall alert + the ONE-SHOT give-up alert
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
        # REGRESSION (presenter): the transient banner literally contains "usage
        # limit" inside "(not your usage limit)" — must NOT be read as a quota cap,
        # so it still gets `continue`.
        self.assertFalse(self.w.is_usage_cap(
            "API Error: Server is temporarily limiting requests "
            "(not your usage limit) · Rate limited"))

    def test_is_usage_cap_recognizes_the_weekly_and_bare_banner_shapes(self):
        # #175 F2: the four real Claude Code banner strings quoted in the
        # ticket, verbatim. The old regex only knew "session"/"usage" before
        # "limit" and required a literal SPACE before "reached/resets" — so
        # the weekly and bare shapes (which use neither qualifier word and
        # separate "limit" from "resets" with a MIDDLE DOT, not a space)
        # fell straight through to the generic nudge path instead of staying
        # bounded.
        self.assertTrue(self.w.is_usage_cap(
            "You've hit your session limit · resets 11:20pm (Europe/Prague)"))
        self.assertTrue(self.w.is_usage_cap(
            "You've hit your weekly limit · resets 12pm (Europe/Prague)"))
        self.assertTrue(self.w.is_usage_cap(
            "You've hit your weekly limit · resets Jul 31, 9pm (Europe/Prague)"))
        self.assertTrue(self.w.is_usage_cap(
            "You've hit your limit · resets 11am (Europe/Prague)"))
        # the transient banner must stay transient after the widening — it
        # literally contains "usage limit" and would false-match a careless
        # widening of the bare-qualifier alternative if that check ever ran
        # before _TRANSIENT_RX.
        self.assertFalse(self.w.is_usage_cap(
            "Server is temporarily limiting requests (not your usage limit) "
            "· Rate limited"))

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
        self.assertTrue(any("ambiguous" in ln for ln in logs))

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
        # first_seen (seeded now-idle = now-600) is in the dedup key
        self.assertIn(str(now - 600), key1, "first_seen must be in the dedup key")
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
        # #33: the waiting footer must survive ≥2 polls before it pings (the
        # persistence gate) — a lone poll that matched the loose footer regex on a
        # lingering / auto-continued dialog false-pinged "čaká na teba". First poll
        # records (silent); the confirmed second poll pings once, never injects keys.
        now = 1_000_000
        cwd = "/devel/asking"
        self._transcript(cwd, [self._OK], 200, now)   # not flagged; 200s stale
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", captures={"%5": self._WAIT_CAP})
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, wait_grace=120)
        self.w.run_once(now=now, **kw)                       # first sight → silent
        self.assertEqual(self.pings, [], "first poll must NOT ping (unconfirmed)")
        logs = self.w.run_once(now=now + 60, **kw)           # confirmed → pings
        self.assertEqual(fake.continues_sent(), 0, "waiting must NEVER inject keys")
        self.assertEqual(len(self.pings), 1, "waiting pings once, on confirmation")
        self.assertIn("asking", self.pings[0][0])
        self.assertTrue(self.pings[0][1].startswith("waiting:"))
        self.assertTrue(any("waiting" in ln for ln in logs))

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
                  state_path=self.state, grace=300, wait_grace=120, wait_clear=90)
        self.w.run_once(now=now, **kw)
        self.assertIn("wait:" + self._SID, self.w.load_state(self.state))
        # answered: prompt footer gone. The key persists briefly (tolerance) then is
        # dropped once the footer has been absent > wait_clear.
        fake.captures["%5"] = "● Committed abc1234\n  done"
        self.w.run_once(now=now + 60, **kw)        # within tolerance → still present
        self.assertIn("wait:" + self._SID, self.w.load_state(self.state))
        self.w.run_once(now=now + 200, **kw)       # absent > wait_clear → dropped
        self.assertNotIn("wait:" + self._SID, self.w.load_state(self.state))

    def test_run_once_waiting_no_reping_on_transcript_jitter(self):
        # THE REPORTED BUG: a multi-question dialog / re-ask loop touches the
        # transcript (idle dips below wait_grace) while the SAME prompt stays open.
        # The episode (footer) dedup must NOT re-ping.
        now = 1_000_000
        cwd = "/devel/jitter"
        self._transcript(cwd, [self._OK], 200, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n", captures={"%5": self._WAIT_CAP})
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, wait_grace=120, wait_clear=90)
        self.w.run_once(now=now, **kw)                       # ping #1
        self._transcript(cwd, [self._OK], 5, now + 60)       # transcript jitters (idle→5)
        self.w.run_once(now=now + 60, **kw)                  # footer still open → no re-ping
        self.w.run_once(now=now + 120, **kw)
        self.assertEqual(len(self.pings), 1, "jitter must not re-ping the same open prompt")

    def test_run_once_ping_mentions_pane_owner(self):
        # the ping must @mention the OWNER of the waiting pane (resolved from that
        # pane's tmux session group) — the watchdog runs headless with no tmux of
        # its own, so it can't use the current-context owner. Two polls: the #33
        # persistence gate pings on the confirmed second poll.
        now = 1_000_000
        cwd = "/devel/ownertest"
        self._transcript(cwd, [self._OK], 200, now)
        fake = _FakeTmux(panes="%5\tclaude\t" + cwd + "\n",
                         captures={"%5": self._WAIT_CAP}, owners={"%5": "marek-12"})
        kw = dict(run=fake, send_fn=self._send, projects_dir=self.projects,
                  state_path=self.state, grace=300, wait_grace=120)
        self.w.run_once(now=now, **kw)              # first sight → silent
        self.w.run_once(now=now + 60, **kw)         # confirmed → pings
        self.assertEqual(self.pings[0][2], "marek", "ping must carry the pane's owner")

    # --- weekly token-usage alert -------------------------------------------
    @staticmethod
    def _wk(pct, resets="W1", model=None):
        lim = {"group": "weekly", "kind": "weekly_all", "percent": pct, "resets_at": resets}
        if model:
            lim["kind"] = "weekly_scoped"
            lim["scope"] = {"model": {"display_name": model}}
        return {"limits": [{"group": "session", "percent": 4}, lim]}

    def test_weekly_percent_picks_highest_active(self):
        u = {"limits": [
            {"group": "session", "percent": 4},
            {"group": "weekly", "kind": "weekly_all", "percent": 41, "resets_at": "R1"},
            {"group": "weekly", "kind": "weekly_scoped", "percent": 80, "resets_at": "R2",
             "scope": {"model": {"display_name": "Opus"}}}]}
        pct, resets, label = self.w.weekly_percent(u)
        self.assertEqual(pct, 80.0)
        self.assertEqual(resets, "R2")
        self.assertIn("Opus", label)

    def test_weekly_percent_none_without_weekly(self):
        self.assertIsNone(self.w.weekly_percent({"limits": [{"group": "session", "percent": 9}]}))
        self.assertIsNone(self.w.weekly_percent({}))

    def test_check_usage_alerts_at_threshold_once_per_window(self):
        st, now = {}, 1_000_000
        def f():
            return self._wk(98, "RW1")
        line = self.w.check_usage(now, st, self._send, fetch=f, threshold=98, interval=900)
        self.assertTrue(line.startswith("usage-alert"))
        self.assertEqual(len(self.pings), 1)
        self.assertIn("98%", self.pings[0][0])
        self.assertTrue(self.pings[0][1].startswith("usage:"))
        # within interval → no re-poll
        self.w.check_usage(now + 100, st, self._send, fetch=f, threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1)
        # after interval, SAME reset window → deduped
        self.w.check_usage(now + 1000, st, self._send, fetch=f, threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1)

    def test_check_usage_below_threshold_no_alert(self):
        st, now = {}, 1_000_000
        self.w.check_usage(now, st, self._send, fetch=lambda: self._wk(80), threshold=98, interval=900)
        self.assertEqual(self.pings, [])

    def test_check_usage_re_alerts_after_window_reset(self):
        st, now = {}, 1_000_000
        self.w.check_usage(now, st, self._send, fetch=lambda: self._wk(99, "W1"),
                           threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1)
        # next week (new resets_at) still >=98 → a fresh alert
        self.w.check_usage(now + 1000, st, self._send, fetch=lambda: self._wk(99, "W2"),
                           threshold=98, interval=900)
        self.assertEqual(len(self.pings), 2)

    def test_check_usage_handles_fetch_failure(self):
        st, now = {}, 1_000_000
        self.w.check_usage(now, st, self._send, fetch=lambda: None, threshold=98, interval=900)
        self.assertEqual(self.pings, [])

    def test_run_once_runs_usage_check_when_fetcher_given(self):
        now = 1_000_000
        fake = _FakeTmux(panes="")        # no panes — isolate the usage job
        logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               usage_fetch=lambda: self._wk(98, "RW"))
        self.assertTrue(any("usage-alert" in ln for ln in logs))
        self.assertEqual(len(self.pings), 1)

    # --- airuleset#212: identity in the usage-alert ping ----------------------
    def test_check_usage_alert_carries_account_identity(self):
        # A bare "Tokeny — 99%" is undecodable on a phone with zero terminal
        # context (this ticket's live incident) — the body must name the
        # account email, the box hostname/unix-account, and WHO it's
        # addressed to.
        st, now = {}, 1_000_000
        with m.patch.object(self.w.usage, "_account_email", return_value="t4user@example.com"), \
                m.patch.object(self.w.usage, "_box_hostname", return_value="subdev"), \
                m.patch.object(self.w.usage, "_local_account", return_value="montalu"):
            self.w.check_usage(now, st, self._send, fetch=lambda: self._wk(99, "RWID"),
                               owner="zbynek", threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1)
        body = self.pings[0][0]
        self.assertIn("t4user@example.com", body)
        self.assertIn("subdev", body)
        self.assertIn("montalu", body)
        self.assertIn("zbynek", body)

    def test_check_usage_alert_survives_unreadable_identity(self):
        # _account_email()/_box_hostname()/_local_account() are never
        # patched here — on THIS test box they resolve to real values (or
        # "" on a broken box), but the send must never raise or blank out
        # regardless.
        st, now = {}, 1_000_000
        self.w.check_usage(now, st, self._send, fetch=lambda: self._wk(99, "RWID2"),
                           owner=None, threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1)
        self.assertIn("99%", self.pings[0][0])

    def test_check_usage_dedups_across_resets_at_subsecond_jitter(self):
        # Live-verified (airuleset#212, montalu@subdev): two fetch_usage()
        # calls for the SAME weekly window, seconds apart, returned TWO
        # DIFFERENT resets_at strings (sub-second jitter) — the raw-string
        # dedup this replaces re-fired on EVERY 15-min poll (11 duplicate
        # pings observed live in one 2.5h window).
        st, now = {}, 1_000_000

        def wk(reset_iso):
            return {"limits": [{"group": "weekly", "kind": "weekly_all",
                                "percent": 99, "resets_at": reset_iso}]}
        self.w.check_usage(now, st, self._send,
                           fetch=lambda: wk("2026-08-08T10:59:59.540561+00:00"),
                           threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1)
        # SAME logical window, sub-second-jittered resets_at — must NOT re-fire.
        self.w.check_usage(now + 900, st, self._send,
                           fetch=lambda: wk("2026-08-08T10:59:59.840558+00:00"),
                           threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1,
                         "sub-second resets_at jitter must not defeat the dedup")
        # A genuinely NEW window (different day) still fires.
        self.w.check_usage(now + 1800, st, self._send,
                           fetch=lambda: wk("2026-08-15T10:59:59.111111+00:00"),
                           threshold=98, interval=900)
        self.assertEqual(len(self.pings), 2)

    def test_check_usage_dedups_a_real_minute_boundary_straddle(self):
        # Adversarial-review finding (airuleset#212 F1/F3): a real 456-sample
        # replay of the fleet's own resets_at history showed EVERY weekly
        # window straddles its reset BOUNDARY (roughly a whole hour) —
        # samples land on BOTH sides, e.g. some at HH:59:59.xx and some at
        # (HH+1):00:00.xx. A bare TRUNCATE-to-minute dedup (the first version
        # of this fix) put those two sides in DIFFERENT buckets and still
        # re-fired ~1 poll in 5. This is the real straddling pair from that
        # history (2026-08-01T11:00:00Z window) — must land in the SAME
        # bucket, unlike the same-minute pair above (which only proves
        # granularity finer than a minute doesn't matter).
        st, now = {}, 1_000_000

        def wk(reset_iso):
            return {"limits": [{"group": "weekly", "kind": "weekly_all",
                                "percent": 99, "resets_at": reset_iso}]}
        self.w.check_usage(now, st, self._send,
                           fetch=lambda: wk("2026-08-01T10:59:59.999219+00:00"),
                           threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1)
        self.w.check_usage(now + 900, st, self._send,
                           fetch=lambda: wk("2026-08-01T11:00:00.012015+00:00"),
                           threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1,
                         "a real minute-boundary straddle must not defeat the dedup")

    def test_check_usage_missing_resets_at_still_alerts_once_and_dedupes(self):
        # Adversarial-review finding (airuleset#212 F4, pre-existing but
        # easy to fix while touching this line): a weekly window with NO
        # resets_at field must still fire its FIRST alert (never silently
        # swallowed by colliding with the "not yet alerted" None sentinel),
        # and must still dedupe on repeat polls with the same missing value.
        st, now = {}, 1_000_000

        def wk_no_reset():
            return {"limits": [{"group": "weekly", "kind": "weekly_all",
                                "percent": 99}]}   # no resets_at key at all
        self.w.check_usage(now, st, self._send, fetch=wk_no_reset,
                           threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1,
                         "a missing resets_at must not silently swallow the alert")
        self.w.check_usage(now + 900, st, self._send, fetch=wk_no_reset,
                           threshold=98, interval=900)
        self.assertEqual(len(self.pings), 1,
                         "repeat polls with the same missing resets_at must dedupe")

    def test_run_once_usage_alert_redirects_stream_persona_owner(self):
        # airuleset#212 root cause: `account_owner` came from the RAW
        # pane_owner() tmux-session-name lookup, with NO knowledge of
        # notify.STREAM_NOTIFY_OWNER (#259) — so a stream persona's own
        # usage alert never got redirected to the real person's thread,
        # unlike every other notification on that box.
        now = 1_000_000
        cwd = "/devel/montalutest"
        self._transcript(cwd, [self._OK], 5, now)
        fake = _FakeTmux(panes="%0\tclaude\t" + cwd + "\n", owners={"%0": "montalu"})
        logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state,
                               usage_fetch=lambda: self._wk(99, "RWSTREAM"))
        self.assertTrue(any("usage-alert" in ln for ln in logs))
        usage_pings = [p for p in self.pings if "Tokeny" in p[0]]
        self.assertEqual(len(usage_pings), 1)
        self.assertEqual(usage_pings[0][2], "zbynek",
                         "a stream persona's raw pane owner must route through "
                         "notify.STREAM_NOTIFY_OWNER, not its own unix account name")

    def test_run_once_skips_usage_without_fetcher(self):
        now = 1_000_000
        fake = _FakeTmux(panes="")
        self.w.run_once(now=now, run=fake, send_fn=self._send,
                        projects_dir=self.projects, state_path=self.state)  # no usage_fetch
        self.assertEqual(self.pings, [])

    # --- wiring --------------------------------------------------------------
    def test_watchdog_subcommand_registered(self):
        self.assertIn("watchdog", airuleset.SUBCOMMANDS)

    def test_validate_watchdog_clean(self):
        self.assertEqual(airuleset._validate_watchdog(), [])

    def test_service_template_runs_watchdog_once(self):
        svc = (airuleset.REPO_DIR / "settings" / "api-watchdog.service.template").read_text()
        self.assertIn("watchdog --once", svc)
        self.assertIn("{{REPO_DIR}}", svc)


class TestWatchdogRepoSweepTimeouts_172(TestCase):
    """#172: jobs 27/28's per-repo network calls used to time out at 90s
    (git fetch) / 45s (each of two `gh issue list` calls) -- with
    `TimeoutStartSec=120` on the systemd unit, ONE hung call could already
    eat most of the whole sweep's budget, and with 40 repos in scope the
    livelock was near-guaranteed. Cut to 15s / 10s so a single hang costs a
    bounded slice of the budget, never most of it."""

    def test_git_fetch_timeout_is_15s_not_90s(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(kw)
            import subprocess as sp
            return sp.CompletedProcess(argv, 0)

        with m.patch("subprocess.run", side_effect=fake_run):
            airuleset._watchdog_git_fetch("/some/repo")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("timeout"), 15)

    def test_issue_counts_fetch_timeout_is_10s_per_call_not_45s(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(kw)
            import subprocess as sp
            return sp.CompletedProcess(argv, 0, stdout="[]")

        with m.patch("subprocess.run", side_effect=fake_run):
            result = airuleset._watchdog_issue_counts_fetch("o/r", 7 * 86400)
        self.assertEqual(result, (0, 0))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(kw.get("timeout") == 10 for kw in calls))


class TestWatchdogBacklogFetch(TestCase):
    """#160 defects 1/4 — `_watchdog_backlog_fetch(cwd)`: does THIS box's
    own slice of the repo at `cwd` still have open backlog work, or None on
    any failure/refusal.

    #238-review-style finding 🔴F1 (this ticket's own review): the ORIGINAL
    version counted the WHOLE REPO via a raw `gh issue list` -- the wrong
    population to verify a session's own `🏁 BACKLOG EMPTY` claim against, a
    full-authority box's /goal loop stops on the CORE/OBLIGATION partition,
    a reduced-authority stream's loop stops on its OWN slice, never the
    whole repo. Rewritten to shell `core-quals --count` / `slice-quals
    --count` -- the SAME commands the `/goal` stop-proof templates
    themselves use -- so this check reads exactly the population the
    session's own claim is about, and inherits their refuse-rather-than-
    guess contract (a non-zero exit prints no number at all)."""

    def _fake(self, stdout="", returncode=0, raises=None):
        calls = []

        def fake_run(argv, **kw):
            calls.append((argv, kw))
            if raises is not None:
                raise raises
            import subprocess as sp
            return sp.CompletedProcess(argv, returncode, stdout=stdout)
        return fake_run, calls

    def test_full_authority_shells_core_quals(self):
        fake_run, calls = self._fake(stdout="3\n")
        with m.patch.object(airuleset, "_repo_root", return_value="/some/repo"), \
             m.patch.object(airuleset, "resolve_authority", return_value="full"), \
             m.patch("subprocess.run", side_effect=fake_run):
            result = airuleset._watchdog_backlog_fetch("/some/repo")
        self.assertEqual(result, 3)
        self.assertEqual(len(calls), 1)
        argv, kw = calls[0]
        self.assertIn("core-quals", argv)
        self.assertIn("--count", argv)
        self.assertNotIn("slice-quals", argv)
        self.assertEqual(kw.get("cwd"), "/some/repo")
        self.assertEqual(kw.get("timeout"), 15)

    def test_reduced_authority_shells_slice_quals(self):
        fake_run, calls = self._fake(stdout="0\n")
        with m.patch.object(airuleset, "_repo_root", return_value="/some/repo"), \
             m.patch.object(airuleset, "resolve_authority",
                           return_value="fork-no-merge"), \
             m.patch("subprocess.run", side_effect=fake_run):
            result = airuleset._watchdog_backlog_fetch("/some/repo")
        self.assertEqual(result, 0)
        argv, _kw = calls[0]
        self.assertIn("slice-quals", argv)
        self.assertNotIn("core-quals", argv)

    def test_authority_resolved_against_the_repo_root_not_the_bare_cwd(self):
        # #160-review-style finding 🟡F3 (this ticket's own review) — a
        # PANE cwd can be a SUBDIRECTORY of the actual repo root; authority
        # must resolve against the ROOT (mirroring `cmd_core_quals`'s/
        # `cmd_slice_quals`'s own `_repo_root()` call inside the child
        # subprocess), never the bare cwd directly, or the parent and the
        # child can pick DIFFERENT profiles and the child refuses forever.
        fake_run, _calls = self._fake(stdout="1\n")
        with m.patch.object(airuleset, "_repo_root",
                           return_value="/some/repo") as rr, \
             m.patch.object(airuleset, "resolve_authority") as ra, \
             m.patch("subprocess.run", side_effect=fake_run):
            ra.return_value = "full"
            airuleset._watchdog_backlog_fetch("/some/repo/sub/dir")
        rr.assert_called_once_with(cwd="/some/repo/sub/dir")
        ra.assert_called_once_with(cwd="/some/repo")

    def test_falls_back_to_the_bare_cwd_when_the_root_cannot_be_resolved(self):
        fake_run, _calls = self._fake(stdout="1\n")
        with m.patch.object(airuleset, "_repo_root", return_value=""), \
             m.patch.object(airuleset, "resolve_authority") as ra, \
             m.patch("subprocess.run", side_effect=fake_run):
            ra.return_value = "full"
            airuleset._watchdog_backlog_fetch("/some/repo")
        ra.assert_called_once_with(cwd="/some/repo")

    def test_nonzero_exit_is_none(self):
        # core-quals/slice-quals REFUSE (non-zero, no number) rather than
        # ever print a false 0 -- this function inherits that refusal.
        fake_run, _calls = self._fake(returncode=1, stdout="")
        with m.patch.object(airuleset, "resolve_authority", return_value="full"), \
             m.patch("subprocess.run", side_effect=fake_run):
            result = airuleset._watchdog_backlog_fetch("/some/repo")
        self.assertIsNone(result)

    def test_subprocess_exception_is_none(self):
        fake_run, _calls = self._fake(raises=OSError("no gh"))
        with m.patch.object(airuleset, "resolve_authority", return_value="full"), \
             m.patch("subprocess.run", side_effect=fake_run):
            result = airuleset._watchdog_backlog_fetch("/some/repo")
        self.assertIsNone(result)

    def test_resolve_authority_exception_is_none(self):
        with m.patch.object(airuleset, "resolve_authority",
                           side_effect=RuntimeError("boom")):
            result = airuleset._watchdog_backlog_fetch("/some/repo")
        self.assertIsNone(result)

    def test_malformed_stdout_is_none(self):
        fake_run, _calls = self._fake(stdout="not a number")
        with m.patch.object(airuleset, "resolve_authority", return_value="full"), \
             m.patch("subprocess.run", side_effect=fake_run):
            result = airuleset._watchdog_backlog_fetch("/some/repo")
        self.assertIsNone(result)

    def test_invokes_this_own_script_via_the_current_interpreter(self):
        # never a bare "airuleset.py" resolved off PATH -- sys.executable +
        # an absolute path to THIS file, so it runs regardless of cwd/PATH.
        fake_run, calls = self._fake(stdout="1\n")
        with m.patch.object(airuleset, "_repo_root", return_value="/some/repo"), \
             m.patch.object(airuleset, "resolve_authority", return_value="full"), \
             m.patch("subprocess.run", side_effect=fake_run):
            airuleset._watchdog_backlog_fetch("/some/repo")
        argv, _kw = calls[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(os.path.isabs(argv[1]))


class TestTier0BuildBlock(TestCase):
    """PreToolUse(Bash) hook block-tier0-local-build.sh — heavy local builds
    (cargo build / cargo test / cargo tauri build / trunk build) are BLOCKED in a
    Tier-0 project (a CLAUDE.md with no local-builds marker); Tier-1/2 markers,
    cheap checks, the inline bypass, and unmanaged dirs are allowed."""

    HOOK = airuleset.REPO_DIR / "hooks" / "block-tier0-local-build.sh"

    def _run(self, cmd, cwd):
        payload = json.dumps({"tool_input": {"command": cmd}, "cwd": cwd})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True)

    def _proj(self, marker=None):
        d = tempfile.mkdtemp()
        content = "# proj\n" + (("<!-- airuleset:local-builds=%s -->\n" % marker) if marker else "")
        Path(d, "CLAUDE.md").write_text(content)
        return d

    def test_blocks_heavy_build_in_tier0(self):
        d = self._proj()                       # no marker = Tier 0
        for cmd in ["cargo build --release", "cargo test", "cargo tauri build", "trunk build"]:
            r = self._run(cmd, d)
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn("BLOCKED", r.stderr)

    def test_allows_cheap_checks_in_tier0(self):
        d = self._proj()
        for cmd in ["cargo check --workspace", "cargo clippy -- -D warnings",
                    "cargo test --no-run", "cargo fmt --all"]:
            self.assertEqual(self._run(cmd, d).returncode, 0, cmd)

    def test_allows_heavy_build_in_tier1_and_tier2(self):
        for marker in ("allowed", "fast-iterate"):
            d = self._proj(marker)
            self.assertEqual(self._run("cargo build --release", d).returncode, 0, marker)

    def test_inline_bypass(self):
        d = self._proj()
        self.assertEqual(self._run("cargo build  # airuleset:build-ok", d).returncode, 0)

    def test_unmanaged_dir_not_enforced(self):
        d = tempfile.mkdtemp()                 # no CLAUDE.md anywhere → not enforced
        self.assertEqual(self._run("cargo build", d).returncode, 0)

    def test_non_build_command_ignored(self):
        d = self._proj()
        self.assertEqual(self._run("git commit -m 'mention cargo build here'", d).returncode, 0)

    def test_wired_into_pretooluse_bash(self):
        cfg = json.loads((airuleset.REPO_DIR / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "") for blk in cfg["hooks"]["PreToolUse"]
                if blk.get("matcher") == "Bash" for h in blk.get("hooks", [])]
        self.assertTrue(any("block-tier0-local-build.sh" in c for c in cmds))


class TestRemoteHosts(TestCase):
    """Deploy-target invariants. The gatekeeper box migrated hosts twice
    (168.119.99.160 → HostKey → Hetzner cx23), and 2026-07-21/22 the marek +
    david sub-dev streams migrated OFF it onto the dedicated subdev VPS
    (tailscale 100.118.174.27; airuleset #23 + odoo-erp #1895 — the old
    david/marek gk accounts are BLOCKED via ForceCommand). A PARTIAL repoint —
    one sub-dev entry moved, another left on the old box — would silently
    deploy to a blocked/stale account. Locks: sub-dev entries share ONE subdev
    host + the key identity, NOTHING but the gatekeeper user targets the gk
    box, and every managed user is present exactly once."""

    GK_HOST = "100.90.94.41"
    # simap/miva1 share marek/david's identity requirement (airuleset#143/
    # #300 — same operator keys as marek, registered on the same subdev box).
    # david2/david3/david4 (airuleset#326, 2026-08-08): three MORE parallel
    # david streams — additional capacity for the same external developer,
    # same subdev box, same gatekeeper_access identity requirement as david.
    SUBDEV_USERS = {"marek", "david", "simap", "miva1",
                    "david2", "david3", "david4"}

    def _subdev_entries(self):
        return [r for r in airuleset.REMOTE_HOSTS
                if r["user"] in self.SUBDEV_USERS]

    def test_all_expected_targets_present_once(self):
        names = [r["name"] for r in airuleset.REMOTE_HOSTS]
        self.assertEqual(len(names), len(set(names)), "duplicate target name")
        for expected in ("dev2", "gatekeeper", "montalu@subdev",
                         "marek@subdev", "david@subdev", "simap@subdev",
                         "montalu2@subdev", "montalu3@subdev",
                         "montalu4@subdev", "miva1@subdev",
                         "david2@subdev", "david3@subdev", "david4@subdev",
                         "montalu5@subdev", "montalu6@subdev",
                         "montalu7@subdev", "montalu8@subdev",
                         "admin@forestshop-dev", "stepan@forestshop-dev",
                         "spinbike-vps"):
            self.assertIn(expected, names)
        self.assertNotIn("montalu@dev1", names,
                         "montalu migrated to subdev (airuleset#33, "
                         "odoo-erp#1895) — the dev1 account is "
                         "ForceCommand-blocked, pushing there would fail")

    def test_forestshop_dev_target_shape(self):
        # airuleset#406 (2026-08-12): forestshop-dev, owner-dedicated Hetzner
        # box for zbynekdrlik/forestshop-app. No tailscale (owner explicitly
        # declined it) — addressed by its own public DNS name (the ticket's
        # own literal ssh address for BOTH accounts), never a raw IP; the raw
        # IP is only a fallback candidate if the DNS name ever stopped
        # resolving, and no evidence of that exists. No `identity` pinned:
        # forestshop_app's own .claude/rules/deploy.md shows
        # `ssh admin@forestshop-dev.newlevel.media` already working with no
        # -i flag from dev1 — i.e. dev1's default key is already authorized,
        # the same "default newlevel key, no identity" shape montalu@subdev
        # already uses.
        entries = {r["name"]: r for r in airuleset.REMOTE_HOSTS
                   if r["name"] in ("admin@forestshop-dev",
                                    "stepan@forestshop-dev")}
        self.assertEqual(len(entries), 2,
                         "both forestshop-dev accounts must be registered")
        for name, user in (("admin@forestshop-dev", "admin"),
                            ("stepan@forestshop-dev", "stepan")):
            e = entries[name]
            self.assertEqual(e["host"], "forestshop-dev.newlevel.media")
            self.assertEqual(e["user"], user)
            self.assertEqual(e["repo_path"], "~/devel/airuleset")
            self.assertNotIn("identity", e,
                             "%s authorizes dev1's default key (live evidence "
                             "in forestshop_app's own deploy.md) — no "
                             "identity pinned, matching montalu@subdev's "
                             "shape" % name)

    def test_forestshop_dev_accounts_share_one_host(self):
        # Both accounts live on the SAME box — sharing a host is exactly the
        # shape #347's shared-host registration-gap audit exists to catch,
        # so this must register as a genuinely shared host too.
        hosts = {r["host"] for r in airuleset.REMOTE_HOSTS
                 if r["name"] in ("admin@forestshop-dev",
                                  "stepan@forestshop-dev")}
        self.assertEqual(hosts, {"forestshop-dev.newlevel.media"})

    def test_forestshop_dev_full_authority_by_default(self):
        # The ticket's own explicit ask: both accounts get FULL autopilot
        # authority (this is the owner's own trusted box, not an external
        # sub-dev contractor stream) — achieved by NOT registering them in
        # AUTHORITY_BY_USER at all, since resolve_authority()/
        # AUTHORITY_BY_USER.get(user, "full") already defaults an
        # unregistered user to "full".
        self.assertNotIn("admin", airuleset.AUTHORITY_BY_USER)
        self.assertNotIn("stepan", airuleset.AUTHORITY_BY_USER)
        self.assertEqual(airuleset.AUTHORITY_BY_USER.get("admin", "full"),
                         "full")
        self.assertEqual(airuleset.AUTHORITY_BY_USER.get("stepan", "full"),
                         "full")

    def test_spinbike_vps_target_shape(self):
        # airuleset#408 (2026-08-12): the SpinBike Hetzner VPS — the first
        # managed target with NO tailscale at all (owner explicitly declined
        # it, spinbike#350). Shape given VERBATIM by the maintainer's own
        # comment on the ticket (issuecomment-5268350062): a keyed public-IP
        # entry, the same shape the pre-existing `gatekeeper` entry already
        # proves REMOTE_HOSTS supports with no code change.
        entries = [r for r in airuleset.REMOTE_HOSTS
                  if r["name"] == "spinbike-vps"]
        self.assertEqual(len(entries), 1, "spinbike-vps target missing")
        e = entries[0]
        self.assertEqual(e["host"], "167.233.245.147")
        self.assertEqual(e["user"], "newlevel")
        self.assertEqual(e["repo_path"], "~/devel/airuleset")
        self.assertEqual(e.get("identity"), "~/.ssh/spinbike_vps")

    def test_spinbike_vps_is_not_tailscale_addressed(self):
        # The whole point of this ticket: unlike every other keyed target in
        # this file (gatekeeper, marek/david/simap/... on subdev, all of
        # which pin an identity AND use a 100.x tailscale IP), spinbike-vps
        # is reached by its raw PUBLIC IPv4 — no MagicDNS name exists for it.
        e = next(r for r in airuleset.REMOTE_HOSTS
                if r["name"] == "spinbike-vps")
        self.assertNotRegex(e["host"], r"^100\.",
                            "spinbike-vps has no tailscale IP by design — "
                            "a 100.x match here would mean the wrong "
                            "(tailscale) address was used instead of the "
                            "documented public IP")

    def test_spinbike_vps_full_authority_by_default(self):
        # Same default-full-authority shape as forestshop-dev — spinbike-vps
        # is not a reduced-authority sub-dev stream, so it must NOT appear
        # in AUTHORITY_BY_USER either.
        self.assertNotIn("newlevel", airuleset.AUTHORITY_BY_USER)

    def test_montalu_subdev_target_shape(self):
        # montalu MIGRATED off dev1 to the subdev VPS (airuleset#33 +
        # odoo-erp#1895, live-verified 2026-07-24): uid 1002, tailscale
        # 100.118.174.27, reachable with the DEFAULT newlevel key — unlike
        # marek/david, the gatekeeper_access identity is NOT authorized for
        # montalu on this box.
        entries = [r for r in airuleset.REMOTE_HOSTS
                   if r["name"] == "montalu@subdev"]
        self.assertEqual(len(entries), 1, "montalu@subdev target missing")
        m = entries[0]
        self.assertEqual(m["host"], "100.118.174.27")
        self.assertEqual(m["user"], "montalu")
        self.assertNotIn("identity", m,
                         "montalu authorizes the DEFAULT newlevel key, not "
                         "gatekeeper_access (unlike marek/david) — "
                         "live-verified at the swap")

    def test_montalu_family_subdev_target_shape(self):
        # airuleset#251: montalu2/3/4 are three MORE full parallel montalu
        # streams ("zhodné s dnešným montalu") — accounts created by
        # gatekeeper (odoo-erp#2961 Phase 1), same subdev box, SAME
        # default-key shape as montalu (never gatekeeper_access).
        for user in ("montalu2", "montalu3", "montalu4"):
            name = "%s@subdev" % user
            entries = [r for r in airuleset.REMOTE_HOSTS if r["name"] == name]
            self.assertEqual(len(entries), 1, "%s target missing" % name)
            e = entries[0]
            self.assertEqual(e["host"], "100.118.174.27")
            self.assertEqual(e["user"], user)
            self.assertNotIn("identity", e,
                             "%s authorizes the DEFAULT newlevel key, "
                             "same as montalu" % user)

    def test_simap_subdev_target_shape(self):
        # simap (airuleset#143, 2026-07-28): 4th sub-dev stream, built by
        # gatekeeper on the SAME subdev box as marek/david, authorized_keys =
        # the SAME operator keys as marek — so it shares marek/david's
        # gatekeeper_access identity requirement, never montalu's
        # default-key path.
        entries = [r for r in airuleset.REMOTE_HOSTS
                   if r["name"] == "simap@subdev"]
        self.assertEqual(len(entries), 1, "simap@subdev target missing")
        s = entries[0]
        self.assertEqual(s["host"], "100.118.174.27")
        self.assertEqual(s["user"], "simap")
        self.assertEqual(s.get("identity"),
                         "~/.secrets/gatekeeper_access_ed25519")

    def test_miva1_subdev_target_shape(self):
        # miva1 (airuleset#300): 5th sub-dev stream, phase-1 isolated -- same
        # shape as simap: built by gatekeeper on the SAME subdev box as
        # marek/david/simap, sharing the operator gatekeeper_access identity
        # requirement, never montalu's default-key path.
        entries = [r for r in airuleset.REMOTE_HOSTS
                   if r["name"] == "miva1@subdev"]
        self.assertEqual(len(entries), 1, "miva1@subdev target missing")
        mv = entries[0]
        self.assertEqual(mv["host"], "100.118.174.27")
        self.assertEqual(mv["user"], "miva1")
        self.assertEqual(mv.get("identity"),
                         "~/.secrets/gatekeeper_access_ed25519")

    def test_david_family_subdev_target_shape(self):
        # david2/david3/david4 (airuleset#326, 2026-08-08): three MORE
        # parallel david streams -- additional capacity for the same
        # external developer (slovnormal odoo dev, no sudo, no prod keys),
        # built by gatekeeper on the SAME subdev box as david itself,
        # sharing david's own gatekeeper_access identity requirement, never
        # montalu's default-key path.
        for user in ("david2", "david3", "david4"):
            name = "%s@subdev" % user
            entries = [r for r in airuleset.REMOTE_HOSTS if r["name"] == name]
            self.assertEqual(len(entries), 1, "%s target missing" % name)
            e = entries[0]
            self.assertEqual(e["host"], "100.118.174.27")
            self.assertEqual(e["user"], user)
            self.assertEqual(e.get("identity"),
                             "~/.secrets/gatekeeper_access_ed25519")

    def test_montalu5_8_subdev_target_shape(self):
        # montalu5/6/7/8 (airuleset#378, odoo-erp#3642): four MORE full
        # parallel montalu streams, same shape as montalu2/3/4 (airuleset#251)
        # -- accounts created by gatekeeper on the SAME subdev box, SAME
        # default-key shape as montalu (never gatekeeper_access).
        for user in ("montalu5", "montalu6", "montalu7", "montalu8"):
            name = "%s@subdev" % user
            entries = [r for r in airuleset.REMOTE_HOSTS if r["name"] == name]
            self.assertEqual(len(entries), 1, "%s target missing" % name)
            e = entries[0]
            self.assertEqual(e["host"], "100.118.174.27")
            self.assertEqual(e["user"], user)
            self.assertNotIn("identity", e,
                             "%s authorizes the DEFAULT newlevel key, "
                             "same as montalu" % user)

    def test_subdev_users_share_one_host_and_identity(self):
        entries = self._subdev_entries()
        self.assertEqual({e["user"] for e in entries}, self.SUBDEV_USERS)
        hosts = {e["host"] for e in entries}
        self.assertEqual(len(hosts), 1,
                         f"subdev entries diverge across hosts: {hosts} "
                         "— partial migration repoint")
        self.assertNotIn(self.GK_HOST, hosts,
                         "marek/david on the gk box are BLOCKED accounts")
        for e in entries:
            self.assertEqual(e.get("identity"),
                             "~/.secrets/gatekeeper_access_ed25519",
                             f"{e['name']} must use the key, never sshpass")

    def test_gatekeeper_user_alone_on_the_gk_box(self):
        gk = [r for r in airuleset.REMOTE_HOSTS if r["host"] == self.GK_HOST]
        self.assertEqual([r["user"] for r in gk], ["gatekeeper"])
        self.assertEqual(gk[0].get("identity"),
                         "~/.secrets/gatekeeper_access_ed25519")

    def test_key_targets_use_tailscale_ips(self):
        # always the tailscale IP — MagicDNS names, public IPs and retired
        # boxes are banned in targets (machine-identities addressing rule)
        for e in self._subdev_entries() + [
                r for r in airuleset.REMOTE_HOSTS if r["host"] == self.GK_HOST]:
            self.assertRegex(e["host"], r"^100\.")
            self.assertNotIn(e["host"], ("100.77.52.43", "168.119.99.160",
                                         "202.148.55.31", "116.203.108.177",
                                         "88.99.170.148"))


class TestCmdPushRuffGate(TestCase):
    """issue #7: `git push` runs as an internal subprocess call inside
    cmd_push, so the PreToolUse pre-push-lint.sh hook (which only fires for a
    real Bash `git push` tool call) never sees the sanctioned `airuleset.py
    push` flow — lint errors could ship. cmd_push must run `ruff check .`
    itself, FIRST (before the test suite, before `git push`), fail-closed."""

    def _fake_run(self, calls, ruff_rc):
        import unittest.mock as m

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            if cmd[:2] == ["ruff", "check"]:
                return m.Mock(returncode=ruff_rc, stdout="", stderr="ruff issues")
            return m.Mock(returncode=0, stdout="", stderr="")
        return fake_run

    def test_push_aborts_when_ruff_fails_before_tests_or_push(self):
        import unittest.mock as m
        calls = []
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=self._fake_run(calls, ruff_rc=1)):
            with m.patch.object(airuleset, "cmd_install") as fake_install:
                with self.assertRaises(SystemExit) as cm:
                    airuleset.cmd_push(args)
                self.assertEqual(cm.exception.code, 1)
                fake_install.assert_not_called()
        self.assertTrue(any(c[:2] == ["ruff", "check"] for c in calls),
                        "ruff must actually run")
        self.assertFalse(any("unittest" in c for c in calls),
                         "ruff must gate BEFORE the test suite runs")
        self.assertFalse(any(c[:2] == ["git", "push"] for c in calls),
                         "ruff must gate BEFORE git push")

    def test_push_runs_ruff_before_tests_and_proceeds_when_clean(self):
        import unittest.mock as m
        calls = []
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=self._fake_run(calls, ruff_rc=0)):
            with m.patch.object(airuleset, "cmd_install"):
                with m.patch.object(airuleset, "REMOTE_HOSTS", []):
                    airuleset.cmd_push(args)
        ruff_idx = next(i for i, c in enumerate(calls) if c[:2] == ["ruff", "check"])
        test_idx = next(i for i, c in enumerate(calls) if "unittest" in c)
        push_idx = next(i for i, c in enumerate(calls) if c[:2] == ["git", "push"])
        self.assertLess(ruff_idx, test_idx, "ruff gate must run BEFORE the test suite")
        self.assertLess(test_idx, push_idx, "tests must still run before git push")

    def test_push_missing_ruff_binary_exits_cleanly_not_a_traceback(self):
        # adversarial-review finding: a missing `ruff` binary raised an
        # UNHANDLED FileNotFoundError straight out of cmd_push (a raw
        # traceback dumped at the user instead of a clean fail-closed
        # message) — push still correctly aborted (the traceback IS fatal),
        # but the failure mode must be a handled, readable message, not an
        # unhandled exception escaping the function.
        import unittest.mock as m
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            if cmd[:2] == ["ruff", "check"]:
                raise FileNotFoundError(2, "No such file or directory", "ruff")
            return m.Mock(returncode=0, stdout="", stderr="")

        args = m.Mock()
        with m.patch("subprocess.run", side_effect=fake_run):
            with m.patch.object(airuleset, "cmd_install") as fake_install:
                with self.assertRaises(SystemExit) as cm:
                    airuleset.cmd_push(args)
                self.assertEqual(cm.exception.code, 1)
                fake_install.assert_not_called()
        self.assertFalse(any("unittest" in c for c in calls),
                         "must abort before the test suite runs")
        self.assertFalse(any(c[:2] == ["git", "push"] for c in calls),
                         "must abort before git push")


class TestCmdPushLocalInstallFailureContinuesToRemotes(TestCase):
    """Adversarial-review CRITICAL finding (plugin-marketplace fix, 2026-08-06):
    cmd_install() can now sys.exit(1) on a still-failing managed-plugin
    install. cmd_push() calls `cmd_install(args)` IN-PROCESS with no
    try/except (step "2. Install locally"), so an uncaught SystemExit
    propagated straight out of cmd_push() BEFORE the remote-deploy loop
    (step 3) ever ran — a single local plugin failure on dev1 would push to
    GitHub and then deploy to ZERO of the 9 remote hosts, including
    montalu2/montalu3/montalu4, the exact accounts this fix exists for.
    This is the SAME class of bug #263 already fixed for a remote
    TimeoutExpired (test_cmd_push_tracks_failures_and_exits_non_zero_when_
    any_occur above) — the local install step needs the identical
    treatment."""

    def _fake_run(self, calls, ssh_rc=0):
        import unittest.mock as m

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=ssh_rc, stdout="ok", stderr="")
        return fake_run

    def test_a_failed_local_install_does_not_abort_the_remote_loop(self):
        import unittest.mock as m
        calls = []
        args = m.Mock()
        fake_hosts = [
            {"name": "dev2", "host": "1.2.3.4", "user": "u", "repo_path": "~/x"},
            {"name": "gk", "host": "5.6.7.8", "user": "g", "repo_path": "~/x",
             "identity": "~/.secrets/k"},
        ]
        with m.patch("subprocess.run", side_effect=self._fake_run(calls)), \
                m.patch.object(airuleset, "cmd_install",
                                side_effect=SystemExit(1)) as fake_install, \
                m.patch.object(airuleset, "REMOTE_HOSTS", fake_hosts):
            with self.assertRaises(SystemExit) as cm:
                airuleset.cmd_push(args)
        fake_install.assert_called_once()
        self.assertEqual(cm.exception.code, 1)
        ssh_calls = [c for c in calls if c and c[0] in ("ssh", "sshpass")]
        self.assertEqual(len(ssh_calls), len(fake_hosts),
                          "every remote host must still be attempted despite "
                          "the local install failure")

    def test_a_healthy_local_install_still_deploys_normally(self):
        # control: a normal (non-failing) local install must not be affected
        # by the new try/except at all.
        import unittest.mock as m
        calls = []
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=self._fake_run(calls)), \
                m.patch.object(airuleset, "cmd_install") as fake_install, \
                m.patch.object(airuleset, "REMOTE_HOSTS", []):
            airuleset.cmd_push(args)   # must NOT raise
        fake_install.assert_called_once()


# --------------------------------------------------------------------------- #
# #358: gk sshd (MaxStartups/fail2ban) drops a burst of rapid ssh connections
# during a push wave -- ssh multiplexing (ControlMaster/ControlPersist) plus
# a bounded, backed-off target-level retry for a connection-closed/reset
# ssh failure. Every test here uses a fake `subprocess.run` / fake `run` --
# no real ssh connection is ever opened.
# --------------------------------------------------------------------------- #

class TestIsSshTransientFailure(TestCase):
    """Distinguishes ssh's OWN connection-establishment failure (a
    MaxStartups/fail2ban-style drop mid-handshake) from an ordinary
    remote-command failure or ssh's own auth-exhaustion failure -- only
    the FORMER is worth a target-level retry."""

    def test_kex_exchange_identification_reset_is_transient(self):
        self.assertTrue(airuleset._is_ssh_transient_failure(
            255,
            "kex_exchange_identification: read: Connection reset by peer\n"))

    def test_ssh_exchange_identification_closed_is_transient(self):
        self.assertTrue(airuleset._is_ssh_transient_failure(
            255,
            "ssh_exchange_identification: Connection closed by remote host\n"))

    def test_bare_connection_closed_by_host_port_is_transient(self):
        self.assertTrue(airuleset._is_ssh_transient_failure(
            255, "Connection closed by 100.90.94.41 port 22\n"))

    def test_bare_connection_reset_by_host_port_is_transient(self):
        # #358 adversarial-review F5: `strings /usr/bin/ssh` confirms
        # "Connection reset by %s port %d" is a real client-side message
        # (sshpkt_vfatal's own ECONNRESET branch) -- the same bare-line
        # shape as the already-covered "closed by" line, but for the
        # reset case. Live-reproduced (ProxyCommand trick) on the real
        # gk incident's own host/port.
        self.assertTrue(airuleset._is_ssh_transient_failure(
            255, "Connection reset by 100.90.94.41 port 22\n"))

    def test_ssh_dispatch_run_fatal_broken_pipe_is_transient(self):
        # #358 adversarial-review F5: a proxy that sends a banner then
        # drops produces "ssh_dispatch_run_fatal: Connection to <host>
        # port <port>: Broken pipe" -- confirmed via `strings` to be
        # ssh's own internal log tag, the same class as the two
        # `*_exchange_identification:` prefixes already covered.
        self.assertTrue(airuleset._is_ssh_transient_failure(
            255,
            "ssh_dispatch_run_fatal: Connection to UNKNOWN port 65535: "
            "Broken pipe\n"))

    def test_auth_failure_is_not_transient(self):
        self.assertFalse(airuleset._is_ssh_transient_failure(
            255, "gatekeeper@100.90.94.41: Permission denied (publickey).\n"))

    def test_non_255_returncode_is_never_transient(self):
        self.assertFalse(airuleset._is_ssh_transient_failure(
            1,
            "kex_exchange_identification: read: Connection reset by peer\n"))

    def test_a_remote_processs_own_connectionreseterror_is_not_transient(self):
        # A REMOTE python process crashing with its own network error must
        # never be misread as ssh's own connection failure -- it carries
        # neither ssh-internal log-tag prefix nor the bare
        # "Connection closed by HOST port N" shape.
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "airuleset.py", line 1, in <module>\n'
            "ConnectionResetError: [Errno 104] Connection reset by peer\n"
        )
        self.assertFalse(airuleset._is_ssh_transient_failure(255, stderr))

    def test_none_stderr_is_safe(self):
        self.assertFalse(airuleset._is_ssh_transient_failure(255, None))

    def test_empty_stderr_is_safe(self):
        self.assertFalse(airuleset._is_ssh_transient_failure(255, ""))


class TestSshRetryMaxAttemptsValue(TestCase):
    """A literal-value lock, deliberately independent of
    `airuleset.SSH_RETRY_MAX_ATTEMPTS` itself -- the OTHER retry tests
    reference the constant dynamically, which would pass unchanged even
    if the constant's own value silently drifted. #358's REVISED root
    cause (issue comment 5245989172: a RANDOM per-connection drop against
    gk's globally-saturated MaxStartups pool, not a per-source ban) is
    what justifies "a few attempts" (3 total, 2 retries) rather than a
    single retry -- pin the literal here so a future edit changing it is
    a deliberate, visible diff."""

    def test_bound_is_three_total_attempts(self):
        self.assertEqual(airuleset.SSH_RETRY_MAX_ATTEMPTS, 3)


class TestSshControlPersistSValue(TestCase):
    """#358 adversarial-review F1 (MAJOR): 60s was sized as "a few seconds
    between the deploy call and the soniox call", but those two calls are
    NOT adjacent -- an account near the front of REMOTE_HOSTS can sit
    behind up to a dozen other targets' own deploy legs (each up to
    REMOTE_DEPLOY_TIMEOUT_S) before its own soniox-phase turn arrives.
    1800s matches REMOTE_DEPLOY_TIMEOUT_S itself. Pinned as its own
    literal-value lock, independent of any test that merely references
    the constant dynamically."""

    def test_persist_window_covers_a_full_remote_deploy_timeout(self):
        self.assertEqual(airuleset.SSH_CONTROL_PERSIST_S,
                          airuleset.REMOTE_DEPLOY_TIMEOUT_S)
        self.assertEqual(airuleset.SSH_CONTROL_PERSIST_S, 1800)


class TestSshRetryBackoffS(TestCase):
    """`AIRULESET_SSH_RETRY_BACKOFF_S` clamped to [1, 300] -- an unclamped
    0/negative value would defeat the backoff entirely (the same class of
    gap #172's AIRULESET_SWEEP_BUDGET_S fix closed elsewhere in this
    file), and an unclamped huge value would stall the whole wave behind
    one flaky target."""

    def test_default_is_60(self):
        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_SSH_RETRY_BACKOFF_S", None)
            self.assertEqual(airuleset._ssh_retry_backoff_s(), 60)

    def test_valid_override_is_honored(self):
        with m.patch.dict(os.environ, {"AIRULESET_SSH_RETRY_BACKOFF_S": "5"}):
            self.assertEqual(airuleset._ssh_retry_backoff_s(), 5)

    def test_zero_is_clamped_to_one(self):
        with m.patch.dict(os.environ, {"AIRULESET_SSH_RETRY_BACKOFF_S": "0"}):
            self.assertEqual(airuleset._ssh_retry_backoff_s(), 1)

    def test_negative_is_clamped_to_one(self):
        with m.patch.dict(os.environ, {"AIRULESET_SSH_RETRY_BACKOFF_S": "-10"}):
            self.assertEqual(airuleset._ssh_retry_backoff_s(), 1)

    def test_huge_value_is_clamped_to_300(self):
        with m.patch.dict(os.environ, {"AIRULESET_SSH_RETRY_BACKOFF_S": "99999"}):
            self.assertEqual(airuleset._ssh_retry_backoff_s(), 300)

    def test_garbage_falls_back_to_default(self):
        with m.patch.dict(os.environ,
                           {"AIRULESET_SSH_RETRY_BACKOFF_S": "notanumber"}):
            self.assertEqual(airuleset._ssh_retry_backoff_s(), 60)


class TestSshMultiplexOpts(TestCase):
    """The ControlMaster/ControlPersist/ControlPath triple that lets a
    repeated connection to the SAME target reuse one already-authenticated
    ssh session -- degrades to [] (plain unmultiplexed ssh) when no
    control_dir is available."""

    def test_no_control_dir_degrades_to_empty(self):
        self.assertEqual(airuleset._ssh_multiplex_opts(None), [])
        self.assertEqual(airuleset._ssh_multiplex_opts(""), [])

    def test_control_dir_produces_the_expected_options(self):
        opts = airuleset._ssh_multiplex_opts("/tmp/arsshcm-xyz")
        self.assertIn("ControlMaster=auto", opts)
        self.assertIn(
            "ControlPersist=%ds" % airuleset.SSH_CONTROL_PERSIST_S, opts)
        self.assertIn("ControlPath=/tmp/arsshcm-xyz/%C", opts)


class TestSshControlDirForPush(TestCase):
    """A bounded, per-run temp directory for ssh ControlMaster sockets --
    degrades to None (never raises) if it cannot be created."""

    def test_creates_a_real_directory(self):
        d = airuleset._ssh_control_dir_for_push()
        try:
            self.assertTrue(d)
            self.assertTrue(os.path.isdir(d))
        finally:
            if d:
                shutil.rmtree(d, ignore_errors=True)

    def test_degrades_to_none_on_failure(self):
        with m.patch("tempfile.mkdtemp", side_effect=OSError("no space")):
            self.assertIsNone(airuleset._ssh_control_dir_for_push())


class TestRedactedSshCmd(TestCase):
    """The manual single-target retry hint printed on an exhausted
    transient failure must never leak the shared subdev password into the
    push log (security-basics.md)."""

    def test_identity_based_cmd_is_unchanged(self):
        cmd = ["ssh", "-i", "/x/k", "-o", "StrictHostKeyChecking=no",
               "gatekeeper@1.2.3.4", "echo hi"]
        self.assertEqual(airuleset._redacted_ssh_cmd(cmd), cmd)

    def test_sshpass_password_is_redacted(self):
        cmd = ["sshpass", "-p", "newlevel", "ssh", "-o",
               "StrictHostKeyChecking=no", "u@1.2.3.4", "echo hi"]
        out = airuleset._redacted_ssh_cmd(cmd)
        self.assertNotIn("newlevel", out)
        self.assertIn("<REDACTED>", out)
        expected = list(cmd)
        expected[2] = "<REDACTED>"
        self.assertEqual(out, expected)

    def test_original_cmd_list_is_not_mutated(self):
        cmd = ["sshpass", "-p", "newlevel", "ssh", "u@1.2.3.4", "echo hi"]
        original = list(cmd)
        airuleset._redacted_ssh_cmd(cmd)
        self.assertEqual(cmd, original)


class TestCmdPushTargetLevelSshRetry(TestCase):
    """A connection-closed/reset ssh failure on ONE target must not leave
    the wave with a permanent hole -- it retries ONLY that target,
    bounded, with a backoff, never re-running ruff/tests/git push."""

    _TRANSIENT_ERR = ("kex_exchange_identification: read: Connection reset "
                       "by peer\n")

    def _fake_hosts(self):
        return [
            {"name": "dev2", "host": "1.2.3.4", "user": "u",
             "repo_path": "~/x"},
            {"name": "gk", "host": "5.6.7.8", "user": "g",
             "repo_path": "~/x", "identity": "~/.secrets/k"},
        ]

    def _scripted_run(self, calls, script):
        counters = {}

        def fake_run(cmd, *a, **k):
            calls.append((list(cmd), dict(k)))
            if cmd and cmd[0] in ("ssh", "sshpass"):
                for needle, results in script.items():
                    if any(needle in str(t) for t in cmd):
                        idx = counters.get(needle, 0)
                        counters[needle] = idx + 1
                        rc, out, err = results[min(idx, len(results) - 1)]
                        return m.Mock(returncode=rc, stdout=out, stderr=err)
            return m.Mock(returncode=0, stdout="ok", stderr="")
        return fake_run

    def _run_push(self, calls, script, backoff_env=None, control_dir=None):
        args = m.Mock()
        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_SSH_RETRY_BACKOFF_S", None)
            if backoff_env is not None:
                os.environ["AIRULESET_SSH_RETRY_BACKOFF_S"] = backoff_env
            with m.patch("subprocess.run",
                          side_effect=self._scripted_run(calls, script)), \
                    m.patch.object(airuleset, "cmd_install"), \
                    m.patch.object(airuleset, "REMOTE_HOSTS",
                                    self._fake_hosts()), \
                    m.patch("time.sleep"), \
                    m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                    return_value=control_dir):
                try:
                    airuleset.cmd_push(args)
                    return None
                except SystemExit as e:
                    return e.code

    def test_transient_failure_succeeds_on_retry_and_is_not_reported_failed(self):
        calls = []
        script = {"5.6.7.8": [(255, "", self._TRANSIENT_ERR),
                               (0, "ok", "")]}
        code = self._run_push(calls, script)
        self.assertIsNone(code)
        gk_calls = [c for c, k in calls if any("5.6.7.8" in str(t) for t in c)]
        self.assertEqual(len(gk_calls), 2)

    def test_retry_is_bounded_and_reports_failed_when_exhausted(self):
        calls = []
        script = {"5.6.7.8": [(255, "", self._TRANSIENT_ERR),
                               (255, "", self._TRANSIENT_ERR),
                               (255, "", self._TRANSIENT_ERR)]}
        code = self._run_push(calls, script)
        self.assertEqual(code, 1)
        gk_calls = [c for c, k in calls if any("5.6.7.8" in str(t) for t in c)]
        self.assertEqual(len(gk_calls), airuleset.SSH_RETRY_MAX_ATTEMPTS,
                          "must stop at the bound, never keep retrying "
                          "forever")

    def test_retry_exhaustion_sleeps_exactly_max_attempts_minus_one_times(self):
        # #358 adversarial-review F3: the SIBLING test above only asserts
        # the ssh CALL count, which is capped by the `range()` bound
        # either way -- it stays green even under a mutant that flips
        # `attempt >= SSH_RETRY_MAX_ATTEMPTS` to `attempt > ...` (which
        # sleeps one extra, useless time after the LAST attempt, printing
        # a misleading "retrying" line for a retry that never happens).
        # Assert the SLEEP count too -- exhausting N attempts must sleep
        # exactly N-1 times, never N.
        calls = []
        script = {"5.6.7.8": [(255, "", self._TRANSIENT_ERR),
                               (255, "", self._TRANSIENT_ERR),
                               (255, "", self._TRANSIENT_ERR)]}
        with m.patch("time.sleep") as sleep_mock:
            args = m.Mock()
            with m.patch("subprocess.run",
                          side_effect=self._scripted_run(calls, script)), \
                    m.patch.object(airuleset, "cmd_install"), \
                    m.patch.object(airuleset, "REMOTE_HOSTS",
                                    self._fake_hosts()), \
                    m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                    return_value=None):
                with self.assertRaises(SystemExit):
                    airuleset.cmd_push(args)
            self.assertEqual(sleep_mock.call_count,
                              airuleset.SSH_RETRY_MAX_ATTEMPTS - 1,
                              "must sleep exactly N-1 times for N exhausted "
                              "attempts -- never sleep after the LAST one, "
                              "which would claim a retry that never happens")

    def test_auth_failure_is_never_retried(self):
        calls = []
        err = "g@5.6.7.8: Permission denied (publickey).\n"
        script = {"5.6.7.8": [(255, "", err), (0, "ok", "")]}
        code = self._run_push(calls, script)
        self.assertEqual(code, 1)
        gk_calls = [c for c, k in calls if any("5.6.7.8" in str(t) for t in c)]
        self.assertEqual(len(gk_calls), 1,
                          "an auth failure must never be retried -- it's "
                          "permanent")

    def test_ordinary_remote_command_failure_is_never_retried(self):
        calls = []
        # rc=1 -- an ordinary failed `git pull`/install, NOT an ssh
        # connection failure -- retrying it would just repeat the same
        # failing remote command for no benefit.
        script = {"5.6.7.8": [(1, "", "error: cannot open FETCH_HEAD\n"),
                               (0, "ok", "")]}
        code = self._run_push(calls, script)
        self.assertEqual(code, 1)
        gk_calls = [c for c, k in calls if any("5.6.7.8" in str(t) for t in c)]
        self.assertEqual(len(gk_calls), 1)

    def test_retry_never_re_runs_ruff_tests_or_git_push(self):
        calls = []
        script = {"5.6.7.8": [(255, "", self._TRANSIENT_ERR),
                               (0, "ok", "")]}
        self._run_push(calls, script)
        ruff_calls = [c for c, k in calls if c[:1] == ["ruff"]]
        test_calls = [c for c, k in calls
                      if len(c) > 2 and c[1:3] == ["-m", "unittest"]]
        git_push_calls = [c for c, k in calls if c[:2] == ["git", "push"]]
        self.assertEqual(len(ruff_calls), 1)
        self.assertEqual(len(test_calls), 1)
        self.assertEqual(len(git_push_calls), 1)

    def test_backoff_env_var_is_honored(self):
        calls = []
        script = {"5.6.7.8": [(255, "", self._TRANSIENT_ERR),
                               (0, "ok", "")]}
        with m.patch("time.sleep") as sleep_mock:
            args = m.Mock()
            with m.patch("subprocess.run",
                          side_effect=self._scripted_run(calls, script)), \
                    m.patch.object(airuleset, "cmd_install"), \
                    m.patch.object(airuleset, "REMOTE_HOSTS",
                                    self._fake_hosts()), \
                    m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                    return_value=None), \
                    m.patch.dict(os.environ,
                                  {"AIRULESET_SSH_RETRY_BACKOFF_S": "7"}):
                airuleset.cmd_push(args)
            sleep_mock.assert_called_once_with(7)

    def test_manual_retry_hint_is_printed_and_password_redacted(self):
        import io
        import contextlib
        calls = []
        hosts = [{"name": "sub", "host": "9.9.9.9", "user": "u",
                   "repo_path": "~/x"}]  # no identity -> sshpass branch
        script = {"9.9.9.9": [(255, "", self._TRANSIENT_ERR),
                               (255, "", self._TRANSIENT_ERR)]}
        buf = io.StringIO()
        args = m.Mock()
        with m.patch("subprocess.run",
                      side_effect=self._scripted_run(calls, script)), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", hosts), \
                m.patch("time.sleep"), \
                m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                return_value=None), \
                contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                airuleset.cmd_push(args)
        out = buf.getvalue()
        self.assertIn("Manual retry once reachable:", out)
        self.assertNotIn("newlevel", out)
        self.assertIn("<REDACTED>", out)


class TestCmdPushSshMultiplexing(TestCase):
    """The deploy loop AND the soniox-key phase share ONE per-run ssh
    ControlMaster socket directory, and it is torn down at the end of the
    push regardless of success/failure."""

    def test_ssh_calls_carry_the_multiplex_options(self):
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=0, stdout="ok", stderr="")

        hosts = [{"name": "gk", "host": "5.6.7.8", "user": "g",
                   "repo_path": "~/x", "identity": "~/.secrets/k"}]
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", hosts), \
                m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                return_value="/tmp/arsshcm-fake"):
            airuleset.cmd_push(args)
        ssh_call = next(c for c in calls if c and c[0] == "ssh")
        self.assertIn("ControlMaster=auto", ssh_call)
        self.assertIn("ControlPath=/tmp/arsshcm-fake/%C", ssh_call)

    def test_sshpass_branch_also_carries_the_multiplex_options(self):
        # #358 adversarial-review F4: the sibling test above only ever
        # used an identity-based (key) host, so a mutant dropping
        # `control_opts` from JUST the sshpass (no-identity) branch --
        # 5 of 13 real REMOTE_HOSTS entries (dev2, montalu/2/3/4) --
        # survived the whole suite untouched. Cover that branch directly.
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=0, stdout="ok", stderr="")

        hosts = [{"name": "dev2", "host": "1.2.3.4", "user": "newlevel",
                   "repo_path": "~/x"}]  # no identity -> sshpass branch
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", hosts), \
                m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                return_value="/tmp/arsshcm-fake-sshpass"):
            airuleset.cmd_push(args)
        ssh_call = next(c for c in calls if c and c[0] == "sshpass")
        self.assertIn("ControlMaster=auto", ssh_call)
        self.assertIn("ControlPath=/tmp/arsshcm-fake-sshpass/%C", ssh_call)

    def test_control_dir_is_cleaned_up_after_a_successful_push(self):
        real_dir = tempfile.mkdtemp(prefix="arsshcm-test-")
        self.addCleanup(shutil.rmtree, real_dir, ignore_errors=True)
        args = m.Mock()
        with m.patch("subprocess.run",
                      return_value=m.Mock(returncode=0, stdout="ok",
                                           stderr="")), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", []), \
                m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                return_value=real_dir):
            airuleset.cmd_push(args)
        self.assertFalse(os.path.isdir(real_dir))

    def test_control_dir_is_cleaned_up_even_when_the_push_fails(self):
        real_dir = tempfile.mkdtemp(prefix="arsshcm-test-")
        self.addCleanup(shutil.rmtree, real_dir, ignore_errors=True)
        hosts = [{"name": "gk", "host": "5.6.7.8", "user": "g",
                   "repo_path": "~/x", "identity": "~/.secrets/k"}]

        def fake_run(cmd, *a, **k):
            if cmd and cmd[0] in ("ssh", "sshpass"):
                return m.Mock(returncode=1, stdout="", stderr="boom")
            return m.Mock(returncode=0, stdout="ok", stderr="")

        args = m.Mock()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", hosts), \
                m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                return_value=real_dir):
            with self.assertRaises(SystemExit):
                airuleset.cmd_push(args)
        self.assertFalse(os.path.isdir(real_dir))

    def test_soniox_phase_receives_the_same_control_opts_as_the_deploy_loop(self):
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append((list(cmd), dict(k)))
            return m.Mock(returncode=0, stdout="ok", stderr="")

        hosts = [{"name": "david@subdev", "host": "9.9.9.9", "user": "david",
                   "repo_path": "~/x",
                   "identity": "~/.secrets/gatekeeper_access_ed25519"}]
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER",
                                {"david": "fork-no-merge"}), \
                m.patch.object(cli_remote, "_soniox_key_line",
                                return_value="SONIOX_API_KEY=x"), \
                m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                return_value="/tmp/arsshcm-fake2"):
            airuleset.cmd_push(args)
        deploy_call = next(
            c for c, k in calls
            if c and c[0] == "ssh"
            and "cat > ~/.soniox.env" not in " ".join(c))
        soniox_call = next(
            c for c, k in calls
            if c and c[0] == "ssh" and "cat > ~/.soniox.env" in " ".join(c))
        self.assertIn("ControlPath=/tmp/arsshcm-fake2/%C", deploy_call)
        self.assertIn("ControlPath=/tmp/arsshcm-fake2/%C", soniox_call)


class TestSharedRemoteHostIps(TestCase):
    """#347: a host backed by MORE than one REMOTE_HOSTS entry (the subdev
    VPS, shared by 11 stream accounts today) is the exact class of box
    where a brand-new Linux account can be hand-provisioned without ever
    gaining its own registration entry -- david2/david3/david4 sat
    unregistered for days before #326 caught it. A single-entry host
    (dev2, gatekeeper) has no such gap by construction."""

    def test_hosts_with_multiple_entries_are_shared(self):
        fake = [
            {"host": "1.1.1.1", "user": "a"},
            {"host": "1.1.1.1", "user": "b"},
            {"host": "2.2.2.2", "user": "c"},
        ]
        with m.patch.object(airuleset, "REMOTE_HOSTS", fake):
            self.assertEqual(airuleset._shared_remote_host_ips(), {"1.1.1.1"})

    def test_single_entry_hosts_are_not_shared(self):
        fake = [{"host": "3.3.3.3", "user": "solo"}]
        with m.patch.object(airuleset, "REMOTE_HOSTS", fake):
            self.assertEqual(airuleset._shared_remote_host_ips(), set())

    def test_empty_remote_hosts_is_safe(self):
        with m.patch.object(airuleset, "REMOTE_HOSTS", []):
            self.assertEqual(airuleset._shared_remote_host_ips(), set())

    def test_real_remote_hosts_flags_the_subdev_vps_as_shared(self):
        # control against the REAL REMOTE_HOSTS constant (no mocking) --
        # proves this actually engages for the real fleet today, not just
        # a hand-built fixture.
        shared = airuleset._shared_remote_host_ips()
        self.assertIn("100.118.174.27", shared,
                       "the subdev VPS (11 REMOTE_HOSTS entries today) must "
                       "be a shared host")


class TestRemoteCmdWithHomeAudit(TestCase):
    """#347: the /home listing must ride an ALREADY-happening ssh
    connection (never a dedicated extra ssh call -- #341's fail2ban-risk
    lesson: never re-probe the same host for a second, unrelated purpose)
    -- and it must NEVER change the original install chain's own exit
    code, since `;`-sequencing after the trailing `ls` would otherwise let
    the LAST command's exit status silently overwrite a genuine
    `git pull`/`install` failure with a false success."""

    def test_marker_present_and_original_failing_exit_code_preserved(self):
        cmd = airuleset._remote_cmd_with_home_audit("false")
        r = subprocess.run(["bash", "-c", cmd], capture_output=True,
                            text=True, timeout=10)
        self.assertEqual(r.returncode, 1,
                          "the ORIGINAL chain's failing exit code must survive "
                          "the trailing audit, never the ls's own exit code")
        self.assertIn(airuleset._HOME_AUDIT_MARKER, r.stdout)

    def test_marker_present_and_original_successful_exit_code_preserved(self):
        cmd = airuleset._remote_cmd_with_home_audit("true")
        r = subprocess.run(["bash", "-c", cmd], capture_output=True,
                            text=True, timeout=10)
        self.assertEqual(r.returncode, 0)
        self.assertIn(airuleset._HOME_AUDIT_MARKER, r.stdout)


class TestParseHomeAuditOutput(TestCase):

    def test_extracts_the_text_after_the_marker(self):
        marker = airuleset._HOME_AUDIT_MARKER
        stdout = "install ok\n%s\nalice\nbob\n" % marker
        self.assertEqual(airuleset._parse_home_audit_output(stdout),
                          "\nalice\nbob\n")

    def test_missing_marker_returns_empty_string(self):
        # e.g. ssh itself failed before the remote shell ever ran the
        # audit -- not this guard's concern, cmd_push's own `failed`
        # tracking already covers that.
        self.assertEqual(airuleset._parse_home_audit_output("Permission denied"), "")

    def test_none_stdout_returns_empty_string(self):
        self.assertEqual(airuleset._parse_home_audit_output(None), "")


class TestUnregisteredHomeAccounts(TestCase):
    """#347: the pure diff function the push-time guard is built on --
    which real /home directory names have NO REMOTE_HOSTS entry for that
    EXACT host."""

    def _hosts(self):
        return [
            {"host": "9.9.9.9", "user": "alice", "name": "a@subdev"},
            {"host": "9.9.9.9", "user": "bob", "name": "b@subdev"},
            {"host": "1.1.1.1", "user": "zed", "name": "zed@other"},
        ]

    def test_flags_a_home_dir_with_no_matching_entry(self):
        with m.patch.object(airuleset, "REMOTE_HOSTS", self._hosts()):
            gap = airuleset.unregistered_home_accounts(
                "9.9.9.9", "alice\nbob\ncarol\n")
        self.assertEqual(gap, ["carol"])

    def test_no_gap_when_every_home_dir_is_registered(self):
        with m.patch.object(airuleset, "REMOTE_HOSTS", self._hosts()):
            gap = airuleset.unregistered_home_accounts(
                "9.9.9.9", "alice\nbob\n")
        self.assertEqual(gap, [])

    def test_a_different_hosts_registration_does_not_count(self):
        # "zed" is registered, but for host 1.1.1.1 -- irrelevant to 9.9.9.9
        with m.patch.object(airuleset, "REMOTE_HOSTS", self._hosts()):
            gap = airuleset.unregistered_home_accounts("9.9.9.9", "zed\n")
        self.assertEqual(gap, ["zed"])

    def test_blank_and_whitespace_lines_are_ignored(self):
        with m.patch.object(airuleset, "REMOTE_HOSTS", self._hosts()):
            gap = airuleset.unregistered_home_accounts(
                "9.9.9.9", "alice\n\n   \nbob\n")
        self.assertEqual(gap, [])

    def test_empty_listing_reports_no_gap(self):
        with m.patch.object(airuleset, "REMOTE_HOSTS", self._hosts()):
            self.assertEqual(airuleset.unregistered_home_accounts("9.9.9.9", ""), [])
            self.assertEqual(airuleset.unregistered_home_accounts("9.9.9.9", None), [])

    def test_gap_is_sorted(self):
        with m.patch.object(airuleset, "REMOTE_HOSTS", self._hosts()):
            gap = airuleset.unregistered_home_accounts(
                "9.9.9.9", "zoe\nalice\nmax\nbob\n")
        self.assertEqual(gap, ["max", "zoe"])

    def test_lost_and_found_is_never_reported_as_a_gap(self):
        # #347 adversarial-review MINOR m2: "lost+found" is a filesystem
        # artifact on a /home that is its own mount point, never a stream
        # account -- reporting it on EVERY push forever trains the reader
        # to ignore the one warning that will someday be real.
        with m.patch.object(airuleset, "REMOTE_HOSTS", self._hosts()):
            gap = airuleset.unregistered_home_accounts(
                "9.9.9.9", "alice\nbob\nlost+found\n")
        self.assertEqual(gap, [])

    def test_a_host_entry_missing_the_user_key_does_not_crash(self):
        # #347 adversarial-review THEORETICAL T3: unregistered_home_accounts
        # guarded r.get("host") but indexed r["user"] unguarded in the same
        # comprehension -- inconsistent, and a KeyError here would crash
        # the whole push loop rather than degrading.
        hosts = [{"host": "9.9.9.9"}]   # no "user" key at all
        with m.patch.object(airuleset, "REMOTE_HOSTS", hosts):
            gap = airuleset.unregistered_home_accounts("9.9.9.9", "alice\n")
        self.assertEqual(gap, ["alice"])


class TestHomeListingTrustworthy(TestCase):
    """#347 adversarial-review MAJOR finding (M1): `_HOME_AUDIT_MARKER`
    being present in stdout only proves the remote SHELL reached the
    audit -- not that `ls -1 /home` actually SUCCEEDED. A hardened or
    root-owned /home makes `ls` fail with its stderr redirected to
    /dev/null, returning an EMPTY listing at rc 0 -- which the marker-only
    check would read as "checked, no gap found" forever, permanently and
    silently defeating the guard. Positive control: the very account this
    ssh connection authenticated AS must appear in any genuine listing
    (that same connection just `cd`'d into its own home's checkout)."""

    def test_a_genuine_listing_containing_the_connecting_user_is_trusted(self):
        self.assertTrue(
            airuleset._home_listing_trustworthy("alice", "alice\nbob\ncarol\n"))

    def test_an_empty_listing_is_never_trusted(self):
        # the exact M1 failure shape: ls failed silently, marker still
        # printed, listing is empty.
        self.assertFalse(airuleset._home_listing_trustworthy("alice", ""))
        self.assertFalse(airuleset._home_listing_trustworthy("alice", None))

    def test_a_listing_missing_the_connecting_users_own_name_is_not_trusted(self):
        # some other partial/corrupted listing that happens to have SOME
        # names but not the one account this connection just proved exists
        # (it authenticated as it) -- still untrustworthy.
        self.assertFalse(
            airuleset._home_listing_trustworthy("alice", "bob\ncarol\n"))


class TestCmdPushSubdevRegistrationAudit(TestCase):
    """#347: cmd_push must audit /home on any SHARED remote host (multiple
    REMOTE_HOSTS entries pointing at the same box -- today only the subdev
    VPS) exactly ONCE per push, riding an ALREADY-successful connection
    (never a dedicated extra ssh call), and print a LOUD, non-blocking
    warning naming any home directory with no matching REMOTE_HOSTS entry
    for that host -- david2/david3/david4 sat unregistered for days before
    #326 caught it live; this is the systemic guard against the SAME class
    recurring for the next stream account."""

    def _fake_hosts(self):
        return [
            {"name": "a@subdev", "host": "9.9.9.9", "user": "alice",
             "repo_path": "~/x", "identity": "~/.secrets/k"},
            {"name": "b@subdev", "host": "9.9.9.9", "user": "bob",
             "repo_path": "~/x", "identity": "~/.secrets/k"},
            {"name": "solo", "host": "1.2.3.4", "user": "solo",
             "repo_path": "~/x"},
        ]

    def _fake_run(self, calls, homes):
        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            if cmd[:2] == ["ruff", "check"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            if "unittest" in cmd:
                return m.Mock(returncode=0, stdout="", stderr="")
            if cmd[:2] == ["git", "push"]:
                return m.Mock(returncode=0, stdout="ok", stderr="")
            if cmd and cmd[0] in ("ssh", "sshpass"):
                remote_cmd = cmd[-1]
                marker = airuleset._HOME_AUDIT_MARKER
                if marker in remote_cmd:
                    out = "ok\n%s\n%s" % (marker, homes)
                    return m.Mock(returncode=0, stdout=out, stderr="")
                return m.Mock(returncode=0, stdout="ok", stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")
        return fake_run

    def _run_push(self, calls, homes):
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=self._fake_run(calls, homes)), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", self._fake_hosts()), \
                m.patch.object(cli_remote, "provision_subdev_soniox_key",
                                return_value=[]):
            airuleset.cmd_push(args)

    def test_audits_exactly_once_for_the_shared_host(self):
        calls = []
        self._run_push(calls, "alice\nbob\n")
        audited = [c for c in calls if c and c[0] in ("ssh", "sshpass")
                   and airuleset._HOME_AUDIT_MARKER in c[-1]]
        self.assertEqual(len(audited), 1,
                          "the /home listing must ride ONE existing "
                          "connection, never a dedicated extra ssh call")

    def test_the_solo_host_is_never_audited(self):
        calls = []
        self._run_push(calls, "alice\nbob\n")
        solo_calls = [c for c in calls if c and c[0] in ("ssh", "sshpass")
                      and "1.2.3.4" in c[-2]]
        self.assertTrue(solo_calls)
        self.assertFalse(any(airuleset._HOME_AUDIT_MARKER in c[-1]
                              for c in solo_calls))

    def test_reports_an_unregistered_home_account_loudly(self):
        import io
        import contextlib
        calls = []
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self._run_push(calls, "alice\nbob\ncarol\n")
        out = buf.getvalue()
        self.assertIn("carol", out)
        self.assertIn("9.9.9.9", out)

    def test_no_warning_when_every_home_dir_is_registered(self):
        import io
        import contextlib
        calls = []
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self._run_push(calls, "alice\nbob\n")
        out = buf.getvalue()
        self.assertNotIn("REGISTRATION GAP", out)

    def test_the_audit_never_blocks_the_push(self):
        # a reported gap is advisory-only -- the push must still succeed
        # (exit code 0) so ONE unrelated stray home dir can never abort
        # deployment to every OTHER already-registered, working account.
        calls = []
        self._run_push(calls, "alice\nbob\ncarol\n")   # must not raise


class TestCmdPushSubdevRegistrationAuditRetryOnFailure(TestCase):
    """#347 adversarial-review CRITICAL finding: `audited_hosts` must NOT
    be marked before the ssh call even runs -- a first entry that fails
    (timeout/auth) before the remote shell ever reaches the appended `ls`
    would otherwise PERMANENTLY skip the audit for the rest of this push,
    with the failure reading IDENTICALLY to "checked, no gap found"
    (unregistered_home_accounts() on an empty/no-marker listing silently
    returns []) -- exactly backwards for a guard whose only job is
    detecting the opposite. Fix: only mark a host audited once the marker
    is CONFIRMED present in real stdout, so a failed first connection lets
    the NEXT entry sharing that host retry; and if EVERY entry for a
    shared host fails, report that honestly as UNVERIFIED, never silently
    as "no gap"."""

    def _fake_hosts(self):
        return [
            {"name": "a@subdev", "host": "9.9.9.9", "user": "alice",
             "repo_path": "~/x", "identity": "~/.secrets/k"},
            {"name": "b@subdev", "host": "9.9.9.9", "user": "bob",
             "repo_path": "~/x", "identity": "~/.secrets/k"},
        ]

    def test_first_entry_ssh_failure_lets_the_second_entry_retry_and_report(self):
        import io
        import contextlib

        def fake_run(cmd, *a, **k):
            if cmd[:2] == ["ruff", "check"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            if "unittest" in cmd:
                return m.Mock(returncode=0, stdout="", stderr="")
            if cmd[:2] == ["git", "push"]:
                return m.Mock(returncode=0, stdout="ok", stderr="")
            if cmd and cmd[0] in ("ssh", "sshpass"):
                remote_cmd = cmd[-1]
                marker = airuleset._HOME_AUDIT_MARKER
                # alice's connection (audited first, since REMOTE_HOSTS
                # order puts her first) fails BEFORE the remote shell ever
                # ran anything -- a genuine ssh auth failure, so the
                # marker never appears in stdout at all.
                if "alice" in cmd[-2]:
                    return m.Mock(returncode=255, stdout="",
                                   stderr="alice@9.9.9.9: Permission denied")
                if marker in remote_cmd:
                    return m.Mock(returncode=0,
                                   stdout="ok\n%s\nalice\nbob\ncarol\n" % marker,
                                   stderr="")
                return m.Mock(returncode=0, stdout="ok", stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        args = m.Mock()
        buf_err = io.StringIO()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", self._fake_hosts()), \
                m.patch.object(cli_remote, "provision_subdev_soniox_key",
                                return_value=[]), \
                contextlib.redirect_stderr(buf_err):
            with self.assertRaises(SystemExit):
                # alice's own failed ssh call is a genuine deploy failure
                # (unrelated to the audit) -- cmd_push exits 1 for THAT
                # reason regardless of the audit outcome.
                airuleset.cmd_push(args)
        err = buf_err.getvalue()
        self.assertIn("carol", err,
                       "the SECOND entry sharing the host must retry the "
                       "audit and still report the real gap")
        self.assertIn("REGISTRATION GAP", err)
        self.assertNotIn("REGISTRATION AUDIT NOT VERIFIED", err)

    def test_every_entry_failing_reports_unverified_not_silently_clean(self):
        import io
        import contextlib

        def fake_run(cmd, *a, **k):
            if cmd[:2] == ["ruff", "check"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            if "unittest" in cmd:
                return m.Mock(returncode=0, stdout="", stderr="")
            if cmd[:2] == ["git", "push"]:
                return m.Mock(returncode=0, stdout="ok", stderr="")
            if cmd and cmd[0] in ("ssh", "sshpass"):
                return m.Mock(returncode=255, stdout="",
                               stderr="x@9.9.9.9: Permission denied")
            return m.Mock(returncode=0, stdout="", stderr="")

        args = m.Mock()
        buf_err = io.StringIO()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", self._fake_hosts()), \
                m.patch.object(cli_remote, "provision_subdev_soniox_key",
                                return_value=[]), \
                contextlib.redirect_stderr(buf_err):
            with self.assertRaises(SystemExit):
                airuleset.cmd_push(args)
        err = buf_err.getvalue()
        self.assertIn("REGISTRATION AUDIT NOT VERIFIED", err)
        self.assertIn("9.9.9.9", err)
        self.assertNotIn("REGISTRATION GAP", err,
                          "an unverified host must never be reported as "
                          "if a real gap-check happened")

    def test_a_marker_present_but_empty_listing_is_never_trusted_as_clean(self):
        # #347 adversarial-review MAJOR finding M1: the ssh call SUCCEEDS
        # (rc=0), the marker DOES print, but `ls -1 /home` itself failed
        # silently (e.g. a hardened/root-owned /home, stderr redirected to
        # /dev/null) and produced NO names at all. The old marker-only
        # check would trust this as "checked, clean" forever. The fix must
        # refuse to trust it -- falling through to retry (here: no second
        # entry, so UNVERIFIED) instead of silently reporting no gap.
        import io
        import contextlib

        def fake_run(cmd, *a, **k):
            if cmd[:2] == ["ruff", "check"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            if "unittest" in cmd:
                return m.Mock(returncode=0, stdout="", stderr="")
            if cmd[:2] == ["git", "push"]:
                return m.Mock(returncode=0, stdout="ok", stderr="")
            if cmd and cmd[0] in ("ssh", "sshpass"):
                remote_cmd = cmd[-1]
                marker = airuleset._HOME_AUDIT_MARKER
                if marker in remote_cmd:
                    # rc 0, marker present, but `ls` itself produced
                    # nothing -- the exact M1 shape.
                    return m.Mock(returncode=0, stdout="ok\n%s\n" % marker,
                                   stderr="")
                return m.Mock(returncode=0, stdout="ok", stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        args = m.Mock()
        buf_err = io.StringIO()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", self._fake_hosts()), \
                m.patch.object(cli_remote, "provision_subdev_soniox_key",
                                return_value=[]):
            with contextlib.redirect_stderr(buf_err):
                airuleset.cmd_push(args)   # must NOT raise (both ssh calls "succeed")
        err = buf_err.getvalue()
        self.assertIn("REGISTRATION AUDIT NOT VERIFIED", err,
                       "an untrustworthy (self-absent) listing must fall "
                       "through to the honest UNVERIFIED report, never a "
                       "silent 'no gap found'")
        self.assertNotIn("REGISTRATION GAP", err)


class TestBlockTestSkipsHook(TestCase):
    """hooks/block-test-skips.sh (issue #10) — mechanical enforcement of
    test-strictness.md's banned skip/tautology syntax. Only ADDED lines
    (this push's diff) in TEST files are scanned; pre-existing tech debt
    the pusher didn't touch must NOT block them."""

    HOOK = "block-test-skips.sh"

    def _run(self, command, cwd):
        payload = json.dumps({"tool_input": {"command": command}})
        # isolate HOME so the audit log write lands in a temp dir, never the real one
        env = dict(os.environ)
        env["HOME"] = tempfile.mkdtemp()
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                              input=payload, text=True, capture_output=True,
                              cwd=cwd, timeout=60, env=env)

    def _repo(self, base_test_content, added_test_content, extra_commit_msg=""):
        """main branch with `base_test_content` already committed (simulating
        pre-existing content), then a second commit APPENDING
        `added_test_content` to the same test file — the diff this push is
        about to send."""
        import shutil
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        def g(*a):
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        test_path = os.path.join(root, "tests", "test_thing.py")
        os.makedirs(os.path.dirname(test_path), exist_ok=True)
        with open(test_path, "w") as fh:
            fh.write(base_test_content)
        g("add", "tests/test_thing.py")
        g("commit", "-qm", "base")
        g("update-ref", "refs/remotes/origin/main", g("rev-parse", "HEAD").stdout.strip())
        with open(test_path, "a") as fh:
            fh.write(added_test_content)
        g("add", "tests/test_thing.py")
        msg = "test: add coverage" + (("\n\n" + extra_commit_msg) if extra_commit_msg else "")
        g("commit", "-qm", msg)
        return root

    def _repo_full(self, rel_path, base_content, new_content):
        """Like `_repo` but (a) an explicit relative path (for testing the
        test-file extension filter against non-code paths like docs/*.md)
        and (b) the second commit REPLACES the file's full content instead
        of only appending — needed to construct two textually DISTANT,
        separately-hunked edits (git diff -U0 groups nearby changes into
        one hunk; only genuinely far-apart edits land in separate hunks)."""
        import shutil
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        def g(*a):
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        full = os.path.join(root, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(base_content)
        g("add", rel_path)
        g("commit", "-qm", "base")
        g("update-ref", "refs/remotes/origin/main", g("rev-parse", "HEAD").stdout.strip())
        with open(full, "w") as fh:
            fh.write(new_content)
        g("add", rel_path)
        g("commit", "-qm", "test: add coverage")
        return root

    def test_blocks_rust_ignore_added(self):
        root = self._repo("", "#[ignore]\nfn test_x() { assert!(1 == 1); }\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("BLOCKED", r.stdout)
        self.assertIn("#[ignore]", r.stdout)

    def test_blocks_pytest_mark_skip(self):
        root = self._repo("", "@pytest.mark.skip\ndef test_x():\n    assert 1 == 1\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("pytest.mark.skip", r.stdout)

    def test_blocks_js_test_skip(self):
        root = self._repo("", "test.skip('does the thing', () => { expect(1).toBe(1); });\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("test.skip", r.stdout)

    def test_blocks_assert_true_tautology(self):
        root = self._repo("", "fn test_x() { assert!(true); }\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("assert!(true)", r.stdout)

    def test_blocks_empty_test_body_python(self):
        root = self._repo("", "def test_x():\n    pass\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("empty test body", r.stdout)

    def test_blocks_empty_test_body_js_arrow(self):
        root = self._repo("", "it('does nothing', () => {});\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("empty test body", r.stdout)

    def test_allows_preexisting_skip_untouched_by_this_push(self):
        # the skip already existed BEFORE this push (base commit) — this push
        # only adds an unrelated real test. Must NOT block on old tech debt.
        base = "#[ignore]\nfn test_old() { assert!(1 == 1); }\n"
        root = self._repo(base, "fn test_new() { assert_eq!(2 + 2, 4); }\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_real_test_addition(self):
        root = self._repo("", "def test_adds_correctly():\n    assert 2 + 2 == 4\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_bypass_with_reason_is_logged(self):
        root = self._repo(
            "", "#[ignore]\nfn test_x() { assert!(1 == 1); }\n",
            extra_commit_msg="# airuleset:test-skip-ok flaky on CI runner, tracked in #99",
        )
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 0, r.stdout)

    # --- adversarial-review findings (autopilot cumulative-diff review) -----

    def test_allows_doc_md_file_merely_containing_spec_substring(self):
        # the test-file heuristic matched ANY path SUBSTRING including
        # ".md" — "docs/xspec.md" contains "spec" as a substring, so prose
        # in a markdown DOC that merely mentions `test.skip(` falsely
        # blocks every push in every managed repo. Must be restricted to
        # actual code extensions.
        root = self._repo_full("docs/xspec.md", "", "Some doc mentioning test.skip('x')\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_still_blocks_real_code_extension_with_spec_in_name(self):
        # sanity check for the extension-filter fix above: a REAL code file
        # (.py) whose name happens to contain "spec" must still be scanned.
        root = self._repo_full("tests/xspec_test.py", "",
                               "def test_x():\n    pass\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_no_phantom_empty_body_match_across_distant_hunks(self):
        # `git diff -U0` added-lines from ALL hunks were joined into ONE
        # blob, ignoring hunk boundaries — a lone `def test_broken():`
        # added in one hunk and a completely UNRELATED lone `pass` added
        # far away in a DIFFERENT hunk concatenate into a phantom
        # "def test_broken():\n    pass" empty-test-body match, even though
        # neither addition has anything to do with the other.
        base = ("def helper_one():\n    return 1\n\n\n"
                "def helper_two():\n    return 2\n\n\n"
                "def helper_three():\n    return 3\n")
        new = ("def helper_one():\n    return 1\n\n\n"
               "def test_broken():\n"                # hunk 1: lone def
               "def helper_two():\n    return 2\n\n\n"
               "def helper_three():\n    return 3\n"
               "    pass\n")                          # hunk 2: lone, unrelated pass
        root = self._repo_full("tests/test_thing.py", base, new)
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_filename_with_space_is_not_silently_skipped(self):
        # `$TEST_CHANGES` was passed UNQUOTED into the python3 argv list —
        # a filename containing a space word-splits into multiple (bogus)
        # argv entries, `git diff -- <bogus-path>` then fails, the
        # exception is swallowed (`except Exception: continue`), and the
        # file is silently never scanned at all — a real #[ignore] added to
        # it sails through unblocked (fail-open).
        root = self._repo_full("tests/test spaced file.py", "",
                               "#[ignore]\nfn test_x() { assert!(1 == 1); }\n")
        r = self._run("git push origin main", root)
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_internal_python3_failure_blocks_with_honest_reason_not_empty(self):
        # any python3-child exit OTHER than the deliberate `sys.exit(2)`
        # must never be reported as if it were a real test-skip violation
        # with an EMPTY reason. Uses a shim that only fails the SECOND
        # (content-scanning) python3 invocation — killing python3 entirely
        # would make the earlier JSON-payload-parsing step fall back in a
        # way that never even reaches the "is this a push?" gate.
        root = self._repo("", "#[ignore]\nfn test_x() { assert!(1 == 1); }\n")
        tmpbin = _path_with_failing_heredoc_python3()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpbin, ignore_errors=True))
        env = dict(os.environ)
        env["PATH"] = tmpbin
        env["HOME"] = tempfile.mkdtemp()
        payload = json.dumps({"tool_input": {"command": "git push origin main"}})
        r = subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                           input=payload, text=True, capture_output=True,
                           cwd=root, timeout=30, env=env)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("internal error", r.stdout.lower() + r.stderr.lower())

    def test_non_push_is_noop(self):
        import shutil
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        r = self._run("ls -la", d)
        self.assertEqual(r.returncode, 0)

    def test_wired_into_pretooluse_bash(self):
        cfg = json.loads((airuleset.REPO_DIR / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "") for blk in cfg["hooks"]["PreToolUse"]
                if blk.get("matcher") == "Bash" for h in blk.get("hooks", [])]
        self.assertTrue(any("block-test-skips.sh" in c for c in cmds))


class TestBlockTestSkipsThreeBranchBase(TestCase):
    """#86 (odoo-erp, 2026-07-26): the diff base was ALWAYS
    origin/<default-branch> ("main"), correct only for a 2-branch dev->main
    repo. On a 3-branch model (develop->staging->main) every work branch
    targets develop, so origin/main lags develop by every already-MERGED,
    green-CI PR — a push whose OWN diff adds no skip pattern at all got
    false-blocked on a sanctioned test.skip() that had already landed on
    develop via an earlier, unrelated PR. Reused the SAME PR-target base
    resolution pre-push-test-check.sh already ships (a feature branch off
    develop targets origin/develop; develop itself promotes to
    origin/staging; staging/default/detached keep the default base)."""

    HOOK = "block-test-skips.sh"

    def _run(self, command, cwd):
        payload = json.dumps({"tool_input": {"command": command}})
        env = dict(os.environ)
        env["HOME"] = tempfile.mkdtemp()
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                              input=payload, text=True, capture_output=True,
                              cwd=cwd, timeout=60, env=env)

    def _mk_3branch_repo(self):
        """A REAL 3-branch repo layout, built with real git commands so the
        diffs the hook sees are genuine — not hand-typed diff text. main and
        develop start equal; develop then gets a commit that ALREADY landed
        a sanctioned test.skip() (simulating a merged, pre-existing PR);
        origin/main stays behind (never advanced past the base), mirroring
        the real odoo-erp gap between main and develop."""
        import shutil
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        def g(*a):
            return subprocess.run(["git", *a], cwd=root, capture_output=True,
                                  text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        open(os.path.join(root, "app.py"), "w").write("def f():\n    return 1\n")
        g("add", "app.py")
        g("commit", "-qm", "base")
        base_sha = g("rev-parse", "HEAD").stdout.strip()
        g("update-ref", "refs/remotes/origin/main", base_sha)

        g("checkout", "-qb", "develop")
        os.makedirs(os.path.join(root, "tests"), exist_ok=True)
        open(os.path.join(root, "tests", "test_old.py"), "w").write(
            "import pytest\n\n@pytest.mark.skip(reason='sanctioned, e2e#2206')\n"
            "def test_old():\n    assert 1 == 1\n"
        )
        g("add", "tests/test_old.py")
        g("commit", "-qm", "test: sanctioned skip already merged into develop")
        g("update-ref", "refs/remotes/origin/develop", g("rev-parse", "HEAD").stdout.strip())

        g("checkout", "-qb", "feature-ceo-guide")
        return root, g

    def test_default_branch_base_false_blocks_on_already_merged_skip(self):
        # RED reproduction (current buggy behavior, before the base-ref
        # fix): this push's OWN diff adds a real test with no skip pattern
        # at all — but because the base is stubbornly origin/main (which
        # never saw the develop-only commit), the diff origin/main...HEAD
        # ALSO contains the already-merged test_old.py skip, and the hook
        # false-blocks a push that introduces zero new skip/tautology code.
        root, g = self._mk_3branch_repo()
        open(os.path.join(root, "tests", "test_new.py"), "w").write(
            "def test_new():\n    assert 2 + 2 == 4\n"
        )
        g("add", "tests/test_new.py")
        g("commit", "-qm", "test: add real coverage for the ceo guide")
        r = self._run("git push origin feature-ceo-guide", root)
        self.assertEqual(r.returncode, 0,
                         "already-merged develop skip false-blocked this push: "
                         + r.stdout)

    def test_real_new_skip_on_a_feature_branch_still_blocks(self):
        # regression guard: a GENUINELY new skip added BY THIS push (on top
        # of the same 3-branch history) must still block — the base-ref fix
        # must not also blind Gate to real violations in the branch's own
        # diff against its real PR target (origin/develop).
        root, g = self._mk_3branch_repo()
        open(os.path.join(root, "tests", "test_new.py"), "w").write(
            "#[ignore]\nfn test_x() { assert!(1 == 1); }\n"
        )
        g("add", "tests/test_new.py")
        g("commit", "-qm", "test: add coverage")
        r = self._run("git push origin feature-ceo-guide", root)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("#[ignore]", r.stdout)


class TestBlockHistoryRewriteHook(TestCase):
    """hooks/block-history-rewrite.sh (issue #11) — two absolute bans that
    previously existed only as prose: git history rewrite (commit-conventions.md)
    and `gh pr merge --admin` branch-protection bypass (pr-merge-policy.md /
    autonomous-quality-discipline.md)."""

    HOOK = "block-history-rewrite.sh"

    def _run(self, command, cwd=None, env_extra=None):
        import shutil
        d = cwd or tempfile.mkdtemp()
        if cwd is None:
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        payload = json.dumps({"tool_input": {"command": command}})
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                              input=payload, text=True, capture_output=True,
                              cwd=d, timeout=30, env=env)

    def test_blocks_rebase_interactive(self):
        r = self._run("git rebase -i HEAD~3")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("rebase", r.stdout)

    def test_blocks_rebase_interactive_long_flag(self):
        r = self._run("git rebase --interactive HEAD~3")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_commit_amend(self):
        r = self._run("git commit --amend -m 'fix'")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("amend", r.stdout)

    def test_blocks_push_force_long_flag(self):
        r = self._run("git push --force origin main")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("force", r.stdout)

    def test_blocks_push_force_short_flag(self):
        r = self._run("git push -f origin main")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_push_force_with_lease(self):
        r = self._run("git push --force-with-lease origin main")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_reset_hard(self):
        r = self._run("git reset --hard HEAD~1")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("reset --hard", r.stdout)

    def test_allows_reset_soft(self):
        r = self._run("git reset --soft HEAD~1")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_reset_plain(self):
        # plain `git reset` (mixed, on unpushed local work) is common and must
        # NOT block — only the destructive --hard form does.
        r = self._run("git reset HEAD~1")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_blocks_gh_admin_merge(self):
        r = self._run("gh pr merge 5 --admin --merge")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("admin", r.stdout)

    def test_allows_normal_gh_merge(self):
        r = self._run("gh pr merge 5 --merge")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_normal_push(self):
        r = self._run("git push origin main")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_normal_commit(self):
        r = self._run("git commit -m 'fix: normal commit'")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_no_false_positive_on_quoted_mention(self):
        # the banned phrase appears only INSIDE a quoted string (a commit
        # message) — must not be tokenized as a real --force/--amend flag.
        r = self._run("git commit -m 'mentions git push --force in the message'")
        self.assertEqual(r.returncode, 0, r.stdout)

    # --- adversarial-review findings (autopilot cumulative-diff review) -----

    def test_commit_message_hash_before_amend_not_swallowed_as_comment(self):
        # tokens_of() stripped everything after the FIRST '#' with a naive
        # `segment.split('#', 1)[0]` — quote-UNAWARE. A commit message
        # containing "#12" (a routine issue reference) truncates the
        # segment mid-quote, shlex then fails to parse it (unmatched quote)
        # and falls back to a naive .split() that drops --amend entirely.
        r = self._run('git commit -m "fix #12: adjust" --amend')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("amend", r.stdout)

    def test_bypass_marker_inside_unrelated_quotes_does_not_bypass_real_violation(self):
        # same class of bug as block-destructive-remote.sh: the marker text
        # merely MENTIONED inside an unrelated quoted string must not
        # bypass a genuinely dangerous, UNRELATED command on the same line.
        cmd = ('echo "we use the marker like # airuleset:history-ok '
               'explaining it" ; git push --force origin main')
        r = self._run(cmd)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("force", r.stdout.lower())

    def test_assignment_prefix_does_not_defeat_detection(self):
        # a leading `VAR=val` token before the real git command must not
        # hide it from strip_prefix (only sudo/env were skipped before).
        r = self._run("GIT_AUTHOR_DATE=x git push --force origin main")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_gh_admin_merge_detected_via_reachable_branch_only(self):
        # locks in the CURRENT detection path (the first, reachable branch
        # at "if len(tk) < 2 or tk[0] != 'git':") so the dead
        # `elif tk[0] == "gh"` INSIDE the git-only branch can be deleted
        # without silently losing gh-admin-merge coverage.
        r = self._run("gh pr merge 5 --admin")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("admin", r.stdout)

    def test_internal_python3_failure_blocks_with_honest_reason_not_empty(self):
        tmpbin = _path_without_python3()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpbin, ignore_errors=True))
        r = self._run("git reset --hard HEAD~1", env_extra={"PATH": tmpbin})
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("internal error", r.stdout.lower() + r.stderr.lower())

    def test_bypass_inline_marker_allows_and_logs(self):
        import shutil
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = self._run(
            "git push --force origin main  # airuleset:history-ok user asked for it",
            env_extra={"HOME": home},
        )
        self.assertEqual(r.returncode, 0, r.stdout)
        log = os.path.join(home, "devel", "airuleset", "audits", "history-rewrite-bypasses.log")
        self.assertTrue(os.path.exists(log), "bypass must be logged")
        self.assertIn("user asked for it", open(log).read())

    def test_bypass_env_var_allows(self):
        r = self._run("git reset --hard HEAD~1", env_extra={"AIRULESET_ALLOW_HISTORY_REWRITE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_wired_into_pretooluse_bash(self):
        cfg = json.loads((airuleset.REPO_DIR / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "") for blk in cfg["hooks"]["PreToolUse"]
                if blk.get("matcher") == "Bash" for h in blk.get("hooks", [])]
        self.assertTrue(any("block-history-rewrite.sh" in c for c in cmds))


class TestBlockDestructiveRemoteHook(TestCase):
    """hooks/block-destructive-remote.sh (issue #13 sub-item 1) — narrow,
    high-confidence subset of no-destructive-remote-actions.md: remote HOST
    shutdown/reboot, a filesystem-root wipe over ssh, and SQL DROP/TRUNCATE
    against a remote database. Deliberately does NOT cover the sanctioned
    deploy flow (systemctl stop/start/restart, taskkill /F) or rm -rf on a
    non-root path — see the hook's own FP-corpus comment header."""

    HOOK = "block-destructive-remote.sh"

    def _run(self, command, cwd=None, env_extra=None):
        import shutil
        d = cwd or tempfile.mkdtemp()
        if cwd is None:
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        payload = json.dumps({"tool_input": {"command": command}})
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                              input=payload, text=True, capture_output=True,
                              cwd=d, timeout=30, env=env)

    # --- true positives ----------------------------------------------------

    def test_blocks_remote_shutdown(self):
        r = self._run('ssh user@host "shutdown -h now"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("shutdown", r.stderr.lower())

    def test_blocks_remote_reboot_with_sudo(self):
        r = self._run('ssh user@host "sudo reboot"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_remote_systemctl_poweroff(self):
        r = self._run('ssh user@host "systemctl poweroff"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_remote_rm_rf_root(self):
        r = self._run('ssh user@host "rm -rf /"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("root", r.stderr.lower())

    def test_blocks_remote_sql_drop_table(self):
        r = self._run("""ssh user@host "psql -c 'DROP TABLE users;'\"""")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_direct_remote_host_truncate(self):
        r = self._run("psql -h prod-db.example.com -c 'TRUNCATE TABLE sessions;'")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_direct_remote_host_drop_database_long_flag(self):
        r = self._run("mysql --host=prod.db.internal -e 'DROP DATABASE app;'")
        self.assertEqual(r.returncode, 2, r.stdout)

    # --- false-positive corpus (sanctioned deploy-flow commands) -----------

    def test_allows_systemctl_stop_start_service(self):
        r = self._run('ssh user@host "systemctl stop myapp && systemctl start myapp"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_rm_rf_temp_dir(self):
        r = self._run('ssh user@host "rm -rf /tmp/build-1234"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_rm_rf_relative_build_dir(self):
        r = self._run('ssh user@host "rm -rf ./build"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_rm_rf_non_root_home_subpath(self):
        r = self._run('ssh user@host "rm -rf ~/old-releases/v1"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_taskkill(self):
        r = self._run('ssh USER@HOST "taskkill /F /IM app.exe"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_sc_start(self):
        r = self._run('ssh USER@HOST "sc start SERVICE"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_remote_piped_read_command(self):
        r = self._run('ssh USER@HOST "tasklist | findstr app"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_journalctl_read(self):
        r = self._run('ssh USER@HOST "journalctl -u SERVICE -n 50"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_airuleset_push_style_ssh_install(self):
        r = self._run(
            'ssh -i ~/.ssh/id_rsa -o StrictHostKeyChecking=no dev2 '
            '"cd ~/devel/airuleset && git pull --ff-only && python3 airuleset.py install"'
        )
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_sshpass_wrapped_install(self):
        r = self._run(
            'sshpass -p newlevel ssh -o StrictHostKeyChecking=no user@dev2 '
            '"cd repo && git pull --ff-only && python3 airuleset.py install"'
        )
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_local_host_sql_drop(self):
        r = self._run("psql -h localhost -c 'DROP TABLE tmp;'")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_delete_from_no_host(self):
        r = self._run("mysql -e 'DELETE FROM sessions WHERE expired'")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_quoted_mention_of_rm_rf(self):
        r = self._run('ssh host "echo this mentions rm -rf / in a comment but is just an echo"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_scp_deploy(self):
        r = self._run("scp binary USER@HOST:/path/to/install/dir/")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_unrelated_git_command(self):
        r = self._run("git push --force origin main")
        self.assertEqual(r.returncode, 0, r.stdout)

    # --- adversarial-review findings (autopilot cumulative-diff review) -----

    def test_bypass_marker_inside_unrelated_quotes_does_not_bypass_real_violation(self):
        # the marker text merely being MENTIONED inside an unrelated quoted
        # string (documentation, an echo) must NOT bypass a genuinely
        # dangerous, UNRELATED command elsewhere in the same line — same
        # class of bug fixed in block-sensitive-staging.sh (d1fde9b): a real
        # bash `#` only starts a comment when it is NOT inside quotes.
        cmd = ('echo "we use the marker like # airuleset:destructive-ok '
               'explaining it" ; ssh host "sudo reboot"')
        r = self._run(cmd)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("reboot", r.stderr.lower())

    def test_assignment_prefix_does_not_defeat_detection(self):
        # `FOO=1 ssh host reboot` — a leading env-var assignment token
        # before the real command must not hide it from strip_prefix
        # (which already skips sudo/env/time/nice/ionice but not `VAR=val`).
        r = self._run('FOO=1 ssh host "reboot"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_psql_dash_H_is_html_output_not_a_host_flag(self):
        # psql's -H means "HTML output", NOT a remote-host flag (that's
        # -h/--host). Treating -H as a host flag makes a purely LOCAL
        # `psql -H -c 'DROP TABLE ...'` false-positive as a remote-DB drop.
        r = self._run("psql -H -c 'DROP TABLE users;'")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_internal_python3_failure_blocks_with_honest_reason_not_empty(self):
        # any python3-child exit OTHER than the deliberate `sys.exit(2)`
        # (missing python3 => 127, permission => 126, an unexpected crash)
        # must never be reported as if it were a real content violation
        # with an EMPTY reason — it's a HOOK MALFUNCTION, say so honestly,
        # and still fail closed (never silently let the command through).
        tmpbin = _path_without_python3()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpbin, ignore_errors=True))
        r = self._run('ssh host "shutdown -h now"', env_extra={"PATH": tmpbin})
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("internal error", r.stderr.lower())
        self.assertNotIn("BLOCKED: destructive command aimed at a REMOTE host/database.\n\n  \n",
                         r.stderr, "must not reuse the empty-reason 'real violation' message")

    # --- bypass --------------------------------------------------------

    def test_bypass_inline_marker_allows_and_logs(self):
        import shutil
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = self._run(
            'ssh host "shutdown -h now"  # airuleset:destructive-ok tested, user approved',
            env_extra={"HOME": home},
        )
        self.assertEqual(r.returncode, 0, r.stdout)
        log = os.path.join(home, "devel", "airuleset", "audits", "destructive-remote-bypasses.log")
        self.assertTrue(os.path.exists(log), "bypass must be logged")
        self.assertIn("user approved", open(log).read())

    def test_bypass_env_var_allows(self):
        r = self._run('ssh host "rm -rf /"',
                       env_extra={"AIRULESET_ALLOW_DESTRUCTIVE_REMOTE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_wired_into_pretooluse_bash(self):
        cfg = json.loads((airuleset.REPO_DIR / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "") for blk in cfg["hooks"]["PreToolUse"]
                if blk.get("matcher") == "Bash" for h in blk.get("hooks", [])]
        self.assertTrue(any("block-destructive-remote.sh" in c for c in cmds))


class TestBlockDestructiveRemoteWinSshHazard(TestCase):
    """hooks/block-destructive-remote.sh's win-* MCP vs ssh extension
    (issue #249) — the ssh path was frictionless while the MCP path got the
    lecture: camera-box PR #989 probed `Get-Process obs64 | ... MainWindowTitle`
    OVER SSH and failed a healthy rig 3x (session 0 / ssh can never see a
    session-1 window via EnumWindows — the title is ALWAYS empty cross-session).
    Scoped to a project that DECLARES a win-* MCP server in .mcp.json (never a
    blanket ssh ban); exempts the sanctioned schtasks .../it bridge the
    windows-remote-gui skill teaches."""

    HOOK = "block-destructive-remote.sh"

    def _win_mcp_dir(self, server_name="win-resolume", entry=None):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        cfg = {"mcpServers": {server_name: entry or {
            "command": "node", "args": ["server.js"]
        }}}
        with open(os.path.join(d, ".mcp.json"), "w") as f:
            json.dump(cfg, f)
        return d

    def _run(self, command, cwd=None, env_extra=None, payload_cwd=None):
        d = cwd or tempfile.mkdtemp()
        if cwd is None:
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        body = {"tool_input": {"command": command}}
        if payload_cwd is not None:
            body["cwd"] = payload_cwd
        payload = json.dumps(body)
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                              input=payload, text=True, capture_output=True,
                              cwd=d, timeout=30, env=env)

    # --- true positive: the exact reported incident -----------------------

    def test_blocks_mainwindowtitle_probe_over_ssh_in_win_mcp_project(self):
        d = self._win_mcp_dir()
        cmd = ('ssh USER@HOST "Get-Process obs64 | Select-Object '
               '-ExpandProperty MainWindowTitle"')
        r = self._run(cmd, cwd=d)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("mainwindow", r.stderr.lower())

    def test_blocks_enumwindows_over_ssh_in_win_mcp_project(self):
        d = self._win_mcp_dir()
        r = self._run('ssh USER@HOST "powershell -c [User32]::EnumWindows(...)"', cwd=d)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_block_message_names_two_context_rule_and_mcp_alternative(self):
        d = self._win_mcp_dir()
        r = self._run('ssh USER@HOST "... MainWindowTitle ..."', cwd=d)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        lowered = r.stderr.lower()
        self.assertIn("mcp", lowered)
        self.assertIn("session 0", lowered)

    # --- never a blanket ssh ban: only gated projects ----------------------

    def test_allows_gui_atom_over_ssh_when_project_has_no_win_mcp(self):
        r = self._run('ssh USER@HOST "... MainWindowTitle ..."')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_gui_atom_when_only_a_non_win_mcp_server_declared(self):
        d = self._win_mcp_dir(server_name="resolume-mcp")
        r = self._run('ssh USER@HOST "... MainWindowTitle ..."', cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_win_prefix_match_is_case_insensitive(self):
        d = self._win_mcp_dir(server_name="WIN-Resolume")
        r = self._run('ssh USER@HOST "... MainWindowTitle ..."', cwd=d)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_win_prefix_must_be_at_the_START_of_the_server_name(self):
        # #249 adversarial-review finding 3: an UNANCHORED "win-" substring
        # match (e.g. a server literally named "a-win-x") must NOT gate —
        # only a genuine `win-*` mcpServers key does.
        d = self._win_mcp_dir(server_name="a-win-x")
        r = self._run('ssh USER@HOST "... MainWindowTitle ..."', cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_symlinked_mcp_json_to_an_endless_read_source_never_hangs(self):
        # #249 adversarial-review finding 1 (MAJOR, live-triggered): a
        # `.mcp.json` symlinked to /dev/zero used to hang EVERY Bash call
        # in that cwd (the old open(path).read() has no bound and follows
        # symlinks). O_NOFOLLOW + a bounded read must refuse it outright —
        # a real timeout (not just a wrong verdict) would mean the bug is
        # still present.
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.symlink("/dev/zero", os.path.join(d, ".mcp.json"))
        payload = json.dumps({"tool_input": {"command": "echo hi"}})
        # timeout derated 10->30s (2026-08-12): the hook measures 0.135s solo,
        # but a 10s bound was hit once by pure CPU starvation while several
        # full suites ran concurrently on this box. A genuine /dev/zero
        # unbounded-read hang is infinite -- 30s still proves "never hangs"
        # while tolerating measured fleet-load scheduling delay.
        r = subprocess.run(
            ["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
            input=payload, text=True, capture_output=True, cwd=d, timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_oversize_mcp_json_never_gates(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        big = {"mcpServers": {"win-x": {"padding": "a" * 100000}}}
        with open(os.path.join(d, ".mcp.json"), "w") as f:
            json.dump(big, f)
        r = self._run('ssh USER@HOST "... MainWindowTitle ..."', cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_malformed_mcp_json_never_crashes_and_never_gates(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, ".mcp.json"), "w") as f:
            f.write("{ not valid json ")
        r = self._run('ssh USER@HOST "... MainWindowTitle ..."', cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # --- sanctioned bridge: schtasks .../it exempts the same atoms --------

    def test_schtasks_interactive_bridge_is_not_blocked(self):
        d = self._win_mcp_dir()
        cmd = ('ssh USER@HOST "schtasks /create /tn T /tr \\"powershell -c '
               'Start-Process obs64.exe\\" /sc once /st 00:00 /ru USER /it /f '
               '&& schtasks /run /tn T && schtasks /delete /tn T /f"')
        r = self._run(cmd, cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_schtasks_without_the_it_flag_does_not_exempt_a_gui_atom(self):
        # #249 adversarial-review finding 3: `schtasks` alone (no `/it`) is
        # NOT the sanctioned bridge — a genuine GUI atom in the same remote
        # command must still block.
        d = self._win_mcp_dir()
        cmd = 'ssh USER@HOST "schtasks /run /tn T && Start-Process obs64.exe"'
        r = self._run(cmd, cwd=d)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    # --- WARN (never block) on other ssh use in a win-mcp project ---------

    def test_warns_but_allows_other_ssh_use_in_win_mcp_project(self):
        d = self._win_mcp_dir()
        r = self._run('ssh USER@HOST "tasklist | findstr app"', cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip(), "expected an additionalContext reminder")
        out = json.loads(r.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("mcp", ctx.lower())

    def test_decoy_hazard_text_in_an_unrelated_block_does_not_add_windows_guidance(self):
        # #249 adversarial-review finding 4: a genuinely UNRELATED block
        # (rm -rf on filesystem root) whose own ECHOED command text happens
        # to CONTAIN the words "GUI-session-dependent" (a decoy comment)
        # must still block for the RIGHT reason, and must NOT print the
        # Windows two-context guidance meant only for a REAL hazard block.
        d = self._win_mcp_dir()
        r = self._run(
            'ssh USER@HOST "rm -rf / # GUI-session-dependent decoy"', cwd=d)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("root", r.stderr.lower())
        self.assertNotIn("Two-context rule", r.stderr)

    def test_no_warn_output_outside_a_win_mcp_project(self):
        r = self._run('ssh USER@HOST "tasklist | findstr app"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_scp_deploy_in_win_mcp_project_stays_unwarned_and_unblocked(self):
        # scp is a file-copy transport, not an ssh remote-command call at
        # all — check_remote_segment() is never reached for it, so it must
        # never be warned about (the ticket's own "stays frictionless" bar).
        d = self._win_mcp_dir()
        r = self._run("scp binary USER@HOST:/path/to/install/dir/", cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    # --- reuses the EXISTING bypass infra, no new escape hatch ------------

    def test_existing_inline_bypass_marker_covers_the_new_hazard_too(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        d = self._win_mcp_dir()
        r = self._run(
            'ssh USER@HOST "... MainWindowTitle ..."'
            '  # airuleset:destructive-ok tested on the real rig',
            cwd=d, env_extra={"HOME": home},
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # --- the JSON payload's own .cwd wins over the hook process's $PWD ----
    # (mirrors block-tier0-local-build.sh's own `dir="${CWD:-$PWD}"` shape —
    # a hook process's own bash cwd is not guaranteed to match the tool's
    # logical cwd, e.g. a worktree-dispatched subagent).

    def test_payload_cwd_wins_even_when_process_pwd_has_no_win_mcp(self):
        bare = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, bare, ignore_errors=True)
        win_mcp = self._win_mcp_dir()
        r = self._run('ssh USER@HOST "... MainWindowTitle ..."',
                       cwd=bare, payload_cwd=win_mcp)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_payload_cwd_wins_even_when_process_pwd_has_win_mcp(self):
        win_mcp = self._win_mcp_dir()
        bare = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, bare, ignore_errors=True)
        r = self._run('ssh USER@HOST "... MainWindowTitle ..."',
                       cwd=win_mcp, payload_cwd=bare)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_autopilot_worker_carries_the_standing_windows_constraint(self):
        # #249 item 3: this survives a reduced-context dispatch even before
        # the hook fires — agents/*.md IS the subagent's own system prompt
        # (measured #104, unlike a skill body which never reaches one).
        t = (airuleset.REPO_DIR / "agents" / "autopilot-worker.md").read_text(
            encoding="utf-8")
        self.assertIn("mcp__win-*", t)
        self.assertIn("session-agnostic", t.lower())
        self.assertIn("NEVER ssh", t)


class TestBlockDestructiveRemoteSecretReadHook(TestCase):
    """hooks/block-destructive-remote.sh's secret-bearing-read extension
    (issue #373) — the odoo-erp#3161 + odoo-erp#3493 incident shape: an ssh
    remote command that cat/less/head/tail/greps a secret-pattern file, or
    bare-dumps printenv/env, whose stdout reaches the transcript. Scoped to
    ssh-remote-command text only (both real incidents are ssh-shaped) — a
    bare LOCAL `cat ~/.env` is deliberately out of scope, see the design
    comment on #373."""

    HOOK = "block-destructive-remote.sh"

    def _run(self, command, cwd=None, env_extra=None):
        import shutil
        d = cwd or tempfile.mkdtemp()
        if cwd is None:
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        payload = json.dumps({"tool_input": {"command": command}})
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                              input=payload, text=True, capture_output=True,
                              cwd=d, timeout=30, env=env)

    # --- true positives — real incident shapes --------------------------

    def test_blocks_cat_of_secret_file_over_ssh(self):
        r = self._run('ssh gk "cat /opt/odoo/mcp.env"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("leaks into the transcript", r.stderr)

    def test_blocks_cat_of_discord_env_over_ssh(self):
        r = self._run('ssh gk "cat ~/.claude/channels/discord/.env"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_the_exact_odoo_erp_3493_incident_shape(self):
        r = self._run(
            "ssh gk \"docker compose exec -T mcp printenv | grep -E '^MCP_'\""
        )
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("leaks into the transcript", r.stderr)

    def test_blocks_bare_printenv_over_ssh(self):
        r = self._run('ssh gk "printenv"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_bare_env_over_ssh(self):
        r = self._run('ssh gk "env"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_grep_against_a_credentials_file_over_ssh(self):
        r = self._run('ssh gk "grep MCP_KMS /opt/app/credentials.json"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_tail_of_env_file_over_ssh(self):
        r = self._run('ssh gk "tail -f /opt/app/.env"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_cat_of_pem_file_over_ssh(self):
        r = self._run('ssh gk "cat /etc/ssl/private/server.pem"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_secret_file_read_wrapped_in_docker_exec(self):
        r = self._run('ssh gk "docker compose exec -T mcp cat /app/mcp.env"')
        self.assertEqual(r.returncode, 2, r.stdout)

    # --- allowed — the sanctioned pipe-to-remote provisioning flow ------

    def test_allows_pipe_local_secret_file_to_remote_write(self):
        r = self._run(
            'cat /home/user/mcp.env | ssh gk "cat > /opt/odoo/mcp.env"'
        )
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_stdout_redirected_cat_of_secret_file(self):
        r = self._run('ssh gk "cat /opt/odoo/mcp.env > /tmp/copy"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_remote_write_via_redirect_named_env(self):
        r = self._run('ssh gk "cat > ~/.env"')
        self.assertEqual(r.returncode, 0, r.stdout)

    # --- adversarial-review finding (#373-review MINOR): Category A's -----
    # --- narrowing must be PER PIPELINE SUB-SEGMENT, not whole-command -----

    def test_unrelated_wc_in_an_earlier_segment_does_not_exempt_a_real_cat_leak(self):
        # the exact review repro: a benign `wc -l` of the SAME file,
        # joined by &&, must never exempt the genuine `cat` read that
        # follows — that would defeat the incident shape this ticket
        # exists to block.
        r = self._run('ssh gk "wc -l /opt/odoo/mcp.env && cat /opt/odoo/mcp.env"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("leaks into the transcript", r.stderr)

    def test_unrelated_awk_in_an_earlier_segment_does_not_exempt_a_real_cat_leak(self):
        r = self._run('ssh gk "awk --version; cat /opt/odoo/mcp.env"')
        self.assertEqual(r.returncode, 2, r.stdout)

    # --- allowed — narrow / template exemptions --------------------------

    def test_allows_env_template_file(self):
        r = self._run('ssh gk "cat /opt/odoo/mcp.env.example"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_named_single_var_printenv(self):
        r = self._run('ssh gk "printenv PATH"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_env_used_as_command_prefix(self):
        r = self._run('ssh gk "env FOO=bar mycommand"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_awk_narrowed_env_dump(self):
        r = self._run(
            "ssh gk \"docker compose exec -T mcp printenv | "
            "awk -F= '{print \\$1\": len=\"length(\\$2)}'\""
        )
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_grep_count_only_against_secret_file(self):
        r = self._run('ssh gk "grep -c MCP_KMS /opt/app/credentials.json"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_sha256sum_of_secret_file(self):
        r = self._run('ssh gk "sha256sum /opt/odoo/mcp.env"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_secret_exec_flow(self):
        r = self._run("python3 airuleset.py secret exec DB -- env")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_cat_of_non_secret_file_over_ssh(self):
        r = self._run('ssh gk "cat /opt/odoo/README.md"')
        self.assertEqual(r.returncode, 0, r.stdout)

    # --- scope: a bare local (non-ssh) read is deliberately NOT covered --

    def test_local_cat_of_dotenv_is_untouched_by_this_extension(self):
        r = self._run('cat ~/.env')
        self.assertEqual(r.returncode, 0, r.stdout)

    # --- bypass ------------------------------------------------------------

    def test_bypass_secret_read_marker_allows_and_logs(self):
        import shutil
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = self._run(
            'ssh gk "cat /opt/odoo/mcp.env"  '
            '# airuleset:secret-read-ok tested, user approved',
            env_extra={"HOME": home},
        )
        self.assertEqual(r.returncode, 0, r.stdout)
        log = os.path.join(home, "devel", "airuleset", "audits",
                           "destructive-remote-bypasses.log")
        self.assertTrue(os.path.exists(log), "bypass must be logged")
        self.assertIn("secret-read-bypass", open(log).read())

    def test_secret_read_bypass_does_not_suppress_unrelated_violations(self):
        # the narrower secret-read bypass must NOT silence a genuinely
        # different category (host power-off) in the same command.
        import shutil
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = self._run(
            'ssh gk "shutdown -h now"  '
            '# airuleset:secret-read-ok this only covers secret reads',
            env_extra={"HOME": home},
        )
        self.assertEqual(r.returncode, 2, r.stdout)


class TestBlockSubdevSshMisuseHook(TestCase):
    """hooks/block-subdev-ssh-misuse.sh (issue #51) — the subdev VPS was
    fail2ban-banned TWICE in one day (2026-07-25) by ad-hoc ssh probes with
    guessed identities (default key as newlevel@/root@, a bare `ssh subdev`
    implying the shell user). The prose dev-rule did not stop a SECOND
    occurrence the same day — this is the mechanical backstop. ALLOW-list
    mirrors airuleset.py's REMOTE_HOSTS exactly: montalu@subdev (no identity
    requirement), marek/david@subdev ONLY with the gatekeeper_access_ed25519
    identity. Everything else — wrong/missing user, marek/david without the
    key — is blocked. A non-subdev target (dev2, gatekeeper) is untouched."""

    HOOK = "block-subdev-ssh-misuse.sh"

    def _run(self, command, cwd=None, env_extra=None):
        import shutil
        d = cwd or tempfile.mkdtemp()
        if cwd is None:
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        payload = json.dumps({"tool_input": {"command": command}})
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                              input=payload, text=True, capture_output=True,
                              cwd=d, timeout=30, env=env)

    # --- blocked shapes ------------------------------------------------

    def test_blocks_newlevel_at_subdev(self):
        r = self._run('ssh newlevel@subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("newlevel", r.stderr)

    def test_blocks_root_at_subdev_ip(self):
        r = self._run('ssh root@100.118.174.27 "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("root", r.stderr)

    def test_blocks_bare_subdev_no_user(self):
        r = self._run('ssh subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("NO user specified", r.stderr)

    def test_blocks_gatekeeper_user_at_subdev(self):
        # gatekeeper's own user is legitimate on ITS box, not on subdev.
        r = self._run('ssh gatekeeper@subdev.newlevel.media "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_marek_without_identity(self):
        r = self._run('ssh marek@subdev.newlevel.media "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("marek", r.stderr)

    def test_blocks_david_without_identity(self):
        r = self._run('ssh david@116.203.108.177 "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("david", r.stderr)

    def test_blocks_marek_with_wrong_identity(self):
        r = self._run('ssh -i ~/.ssh/id_rsa marek@subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_simap_without_identity(self):
        # simap (airuleset#143) shares marek/david's identity requirement.
        r = self._run('ssh simap@subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("simap", r.stderr)

    def test_blocks_simap_with_wrong_identity(self):
        r = self._run('ssh -i ~/.ssh/id_rsa simap@subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_miva1_without_identity(self):
        # miva1 (airuleset#300) shares marek/david/simap's identity
        # requirement. Asserts the SPECIFIC reason (not just "miva1"
        # anywhere in stderr) -- an unauthorized-user-shaped message would
        # also contain "miva1" and pass this test for the wrong reason if
        # the allow-list entry (check_target's `user in (...)` tuple) were
        # ever removed by mistake.
        r = self._run('ssh miva1@subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("without -i", r.stderr)
        self.assertIn("miva1", r.stderr)

    def test_blocks_miva1_with_wrong_identity(self):
        r = self._run('ssh -i ~/.ssh/id_rsa miva1@subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_david_family_without_identity(self):
        # david2/david3/david4 (airuleset#326) share david's own identity
        # requirement. Asserts the SPECIFIC reason (not just the username
        # anywhere in stderr), same discipline as the miva1 test above.
        for user in ("david2", "david3", "david4"):
            r = self._run('ssh %s@subdev "ls"' % user)
            self.assertEqual(r.returncode, 2, user + " " + r.stdout)
            self.assertIn("without -i", r.stderr)
            self.assertIn(user, r.stderr)

    def test_blocks_david_family_with_wrong_identity(self):
        for user in ("david2", "david3", "david4"):
            r = self._run('ssh -i ~/.ssh/id_rsa %s@subdev "ls"' % user)
            self.assertEqual(r.returncode, 2, user + " " + r.stdout)

    def test_blocks_scp_wrong_user(self):
        r = self._run("scp file.txt newlevel@subdev:/tmp/")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_rsync_wrong_user(self):
        r = self._run("rsync -avz ./local/ newlevel@subdev:/remote/")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_sftp_wrong_user(self):
        r = self._run("sftp newlevel@subdev")
        self.assertEqual(r.returncode, 2, r.stdout)

    # --- allowed shapes --------------------------------------------------

    def test_allows_montalu_default_key(self):
        r = self._run('ssh montalu@subdev "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_montalu_family_default_key(self):
        # airuleset#251: montalu2/3/4 share montalu's own no-identity-
        # required shape (three more full parallel montalu streams).
        for user in ("montalu2", "montalu3", "montalu4"):
            r = self._run('ssh %s@subdev "ls"' % user)
            self.assertEqual(r.returncode, 0, user + " " + r.stdout + r.stderr)

    def test_allows_montalu5_8_default_key(self):
        # airuleset#378, odoo-erp#3642: montalu5/6/7/8 share montalu's own
        # no-identity-required shape (four MORE full parallel montalu
        # streams).
        for user in ("montalu5", "montalu6", "montalu7", "montalu8"):
            r = self._run('ssh %s@subdev "ls"' % user)
            self.assertEqual(r.returncode, 0, user + " " + r.stdout + r.stderr)

    def test_blocks_unknown_montalu9_at_subdev(self):
        # #378: widening the montalu[2345678] allow-list must NOT widen it
        # into an open-ended prefix match -- an unregistered sibling
        # (montalu9) stays blocked as an unauthorized user, the same
        # discipline the miva1/david-family tests already assert.
        r = self._run('ssh montalu9@subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("unauthorized user", r.stderr)
        self.assertIn("montalu9", r.stderr)

    def test_allows_montalu_via_sshpass(self):
        r = self._run(
            'sshpass -p newlevel ssh -o StrictHostKeyChecking=no montalu@subdev "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_marek_with_gatekeeper_identity(self):
        r = self._run(
            'ssh -i ~/.secrets/gatekeeper_access_ed25519 marek@subdev "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_david_with_gatekeeper_identity(self):
        r = self._run(
            'ssh -i ~/.secrets/gatekeeper_access_ed25519 david@subdev.newlevel.media "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_simap_with_gatekeeper_identity(self):
        r = self._run(
            'ssh -i ~/.secrets/gatekeeper_access_ed25519 simap@subdev "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_miva1_with_gatekeeper_identity(self):
        r = self._run(
            'ssh -i ~/.secrets/gatekeeper_access_ed25519 miva1@subdev "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_david_family_with_gatekeeper_identity(self):
        for user in ("david2", "david3", "david4"):
            r = self._run(
                'ssh -i ~/.secrets/gatekeeper_access_ed25519 %s@subdev "ls"' % user)
            self.assertEqual(r.returncode, 0, user + " " + r.stdout + r.stderr)

    def test_allows_fused_identity_flag(self):
        r = self._run('ssh -i~/.secrets/gatekeeper_access_ed25519 david@subdev "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_scp_montalu(self):
        r = self._run("scp file.txt montalu@subdev:/tmp/")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_rsync_marek_with_identity(self):
        r = self._run(
            "rsync -avz -e 'ssh -i ~/.secrets/gatekeeper_access_ed25519' "
            "./local/ marek@subdev:/remote/")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # --- non-subdev traffic is completely untouched ----------------------

    def test_non_subdev_dev2_untouched(self):
        r = self._run('ssh newlevel@100.82.64.27 "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_non_subdev_gatekeeper_untouched(self):
        r = self._run(
            'ssh -i ~/.secrets/gatekeeper_access_ed25519 gatekeeper@100.90.94.41 "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unrelated_command_untouched(self):
        r = self._run("git push origin main")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # --- bypass -----------------------------------------------------------

    def test_bypass_inline_marker_allows_and_logs(self):
        import shutil
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = self._run(
            'ssh root@subdev "ls"  # airuleset:subdev-ssh-ok tested, user approved',
            env_extra={"HOME": home},
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = os.path.join(home, "devel", "airuleset", "audits", "subdev-ssh-bypasses.log")
        self.assertTrue(os.path.exists(log), "bypass must be logged")
        self.assertIn("user approved", open(log).read())

    def test_marker_mentioned_inside_unrelated_quotes_does_not_bypass_real_violation(self):
        # the marker text merely being MENTIONED inside an unrelated quoted
        # string (documentation, an echo, a commit message body) must NOT
        # bypass a genuinely dangerous UNRELATED command elsewhere on the
        # same line — same class of fix as block-destructive-remote.sh /
        # block-sensitive-staging.sh (test_block_staged_content_values.py).
        cmd = ('echo "we use the marker like # airuleset:subdev-ssh-ok '
               'explaining it" ; ssh root@subdev "ls"')
        r = self._run(cmd)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("root", r.stderr)

    # --- gatekeeper root@subdev identity (#68) ------------------------------
    # `hooks/block-subdev-ssh-misuse.sh` had no allowed identity for the
    # gatekeeper VPS's own sanctioned root@subdev + ~/.ssh/subdev_admin
    # identity, so every legitimate gatekeeper->subdev bounce-nudge needed a
    # bypass marker. Allow it explicitly (-i .../subdev_admin) AND via the
    # box's own ~/.ssh/config `Host subdev` stanza (the real process-subdev
    # nudge shape: no explicit user, no explicit -i on the command line).

    def _home_with_ssh_config(self, config_text):
        import shutil
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        ssh_dir = os.path.join(home, ".ssh")
        os.makedirs(ssh_dir, exist_ok=True)
        if config_text is not None:
            with open(os.path.join(ssh_dir, "config"), "w") as f:
                f.write(config_text)
        return home

    def test_allows_explicit_root_with_subdev_admin_identity(self):
        r = self._run('ssh -i ~/.ssh/subdev_admin root@subdev "ls"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_blocks_root_with_wrong_identity(self):
        r = self._run('ssh -i ~/.ssh/id_rsa root@subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("root", r.stderr)

    def test_blocks_bare_root_without_identity(self):
        r = self._run('ssh root@subdev "ls"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("root", r.stderr)

    def test_allows_bare_subdev_via_gatekeeper_sshconfig(self):
        # the REAL process-subdev nudge shape: no explicit user, no explicit
        # -i on the command line at all -- relies entirely on the box's own
        # ~/.ssh/config `Host subdev` stanza (User root, IdentityFile
        # ~/.ssh/subdev_admin), exactly as deployed on the gatekeeper VPS.
        home = self._home_with_ssh_config(
            "Host subdev\n"
            "    HostName 100.118.174.27\n"
            "    User root\n"
            "    IdentityFile ~/.ssh/subdev_admin\n"
            "    IdentitiesOnly yes\n"
        )
        r = self._run('ssh subdev "sudo -n -u david -H tmux send-keys -t david hi"',
                      env_extra={"HOME": home})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_bare_subdev_still_blocked_without_matching_sshconfig(self):
        # dev1 has no such Host subdev block -- the base "NO user specified"
        # behavior must be completely unaffected.
        home = self._home_with_ssh_config(None)
        r = self._run('ssh subdev "ls"', env_extra={"HOME": home})
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("NO user specified", r.stderr)

    def test_bare_subdev_blocked_when_sshconfig_points_elsewhere(self):
        # a Host subdev block resolving to a DIFFERENT user must not
        # accidentally authorize anything.
        home = self._home_with_ssh_config(
            "Host subdev\n"
            "    User newlevel\n"
            "    IdentityFile ~/.ssh/id_rsa\n"
        )
        r = self._run('ssh subdev "ls"', env_extra={"HOME": home})
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_bare_subdev_blocked_when_sshconfig_has_wrong_identity_basename(self):
        home = self._home_with_ssh_config(
            "Host subdev\n"
            "    User root\n"
            "    IdentityFile ~/.ssh/id_rsa\n"
        )
        r = self._run('ssh subdev "ls"', env_extra={"HOME": home})
        self.assertEqual(r.returncode, 2, r.stdout)

    # --- fail-closed on internal error -------------------------------------

    def test_internal_python3_failure_blocks_with_honest_reason_not_empty(self):
        tmpbin = _path_without_python3()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpbin, ignore_errors=True))
        r = self._run('ssh newlevel@subdev "ls"', env_extra={"PATH": tmpbin})
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("internal error", r.stderr.lower())

    def test_wired_into_pretooluse_bash(self):
        cfg = json.loads((airuleset.REPO_DIR / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "") for blk in cfg["hooks"]["PreToolUse"]
                if blk.get("matcher") == "Bash" for h in blk.get("hooks", [])]
        self.assertTrue(any("block-subdev-ssh-misuse.sh" in c for c in cmds))


class TestPreWriteScriptCheckHook(TestCase):
    """hooks/pre-write-script-check.sh (issue #13 sub-item 2,
    script-failure-policy.md) — PreToolUse(Write|Edit): new .sh files need
    `set -euo pipefail`; new Python content must not swallow an exception
    with a bare `pass`."""

    HOOK = "pre-write-script-check.sh"

    def _run_write(self, file_path, content, env_extra=None, cwd=None):
        payload = json.dumps({"tool_input": {"file_path": file_path, "content": content}})
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                              input=payload, text=True, capture_output=True,
                              timeout=30, env=env, cwd=cwd)

    def _run_edit(self, file_path, new_string, old_string="x", env_extra=None, cwd=None):
        payload = json.dumps({"tool_input": {"file_path": file_path,
                                              "old_string": old_string,
                                              "new_string": new_string}})
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
                              input=payload, text=True, capture_output=True,
                              timeout=30, env=env, cwd=cwd)

    # --- .sh shebang / pipefail check (Write only) --------------------------

    def test_blocks_new_sh_missing_pipefail(self):
        r = self._run_write("new.sh", "#!/usr/bin/env bash\necho hi\n")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("pipefail", r.stderr.lower())

    def test_allows_new_sh_with_pipefail(self):
        r = self._run_write("good.sh", "#!/usr/bin/env bash\nset -euo pipefail\necho hi\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_edit_of_sh_file_not_checked_for_pipefail(self):
        # Edit payloads are a partial diff — the header check is Write-only
        # (documented gap). An Edit touching an unrelated body line must
        # never be flagged for a header property it cannot see.
        r = self._run_edit("existing.sh", "echo more stuff\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    # --- python except:pass check (Write + Edit) -----------------------------

    def test_blocks_bare_except_pass_write(self):
        content = "def f():\n    try:\n        risky()\n    except:\n        pass\n"
        r = self._run_write("bad.py", content)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("pass", r.stderr.lower())

    def test_blocks_except_exception_pass_write(self):
        content = "def f():\n    try:\n        risky()\n    except Exception:\n        pass\n"
        r = self._run_write("bad2.py", content)
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_except_typed_pass_on_edit(self):
        new_string = "    try:\n        risky()\n    except OSError:\n        pass\n"
        r = self._run_edit("existing.py", new_string)
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_allows_except_with_logging(self):
        content = ("def f():\n    try:\n        risky()\n"
                   "    except Exception as e:\n        logger.error('failed: %s', e)\n")
        r = self._run_write("good.py", content)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_except_pass_not_sole_statement(self):
        # `pass` followed by a real statement at the same indent is not a
        # silent swallow — the block does real work.
        content = ("def f():\n    try:\n        risky()\n"
                   "    except Exception:\n        pass\n        do_more()\n")
        r = self._run_write("edge.py", content)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_non_script_file(self):
        r = self._run_write("notes.txt", "no shebang needed here\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_edit_with_no_except(self):
        r = self._run_edit("mid_body.py", "    logger.info('just a normal edit')\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    # --- adversarial-review findings (autopilot cumulative-diff review) -----

    def test_large_payload_over_arg_max_does_not_false_block(self):
        # >~128KB (Linux MAX_ARG_STRLEN) used to be passed as a SINGLE argv
        # string to the embedded python3 child ("python3 - $PAYLOAD") — that
        # blows execve's per-argument limit ("Argument list too long") and
        # the hook then falsely BLOCKS every large Write/Edit with an EMPTY
        # reason, regardless of file type or actual content. A legitimate
        # large write with ZERO violations must not be blocked.
        big_content = "".join("print('line %d')\n" % i for i in range(20000))
        self.assertGreater(len(big_content), 131072)
        r = self._run_write("big.py", big_content)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_blocks_one_liner_except_pass(self):
        # `except X: pass` on ONE line evades the old multi-line-only regex
        # entirely — same silent-swallow the multi-line form is blocked for.
        content = "def f():\n    try:\n        risky()\n    except Exception: pass\n"
        r = self._run_write("oneliner.py", content)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("pass", r.stderr.lower())

    def test_allows_except_pass_text_inside_docstring(self):
        # the OLD line-regex scan has no syntax awareness — literal
        # "except X:\n    pass" text sitting inside a DOCSTRING (documenting
        # the very anti-pattern this hook bans, e.g. in a code example) is
        # not real code and must never false-block.
        content = ('def f():\n    """Example of what NOT to do:\n'
                   '    except Exception:\n        pass\n    """\n    return 1\n')
        r = self._run_write("docstring_example.py", content)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_edit_carrying_preexisting_except_pass_as_unique_match_context(self):
        # Edit's old_string/new_string often carry surrounding UNCHANGED
        # lines purely to make old_string a unique match — an except-pass
        # block that already existed IDENTICALLY in old_string is not being
        # introduced by this edit, only carried as context. Only "return 1"
        # -> "return 2" actually changed.
        old = "    except Exception:\n        pass\n    return 1\n"
        new = "    except Exception:\n        pass\n    return 2\n"
        r = self._run_edit("existing_ctx.py", new, old_string=old)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_edit_introducing_new_except_pass_still_blocked(self):
        # the old_string carve-out must not become a blanket bypass — a
        # GENUINELY new except-pass (absent from old_string) still blocks.
        old = "    return 1\n"
        new = "    except Exception:\n        pass\n    return 1\n"
        r = self._run_edit("existing_new.py", new, old_string=old)
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_allows_full_rewrite_of_existing_sh_file(self):
        # a Write is a FULL-FILE REWRITE of a legacy script ALREADY on disk —
        # same "never retroactively flag pre-existing content" principle as
        # the except-pass check. Only a genuinely NEW file is enforced.
        import shutil
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        existing = os.path.join(tmp, "legacy.sh")
        with open(existing, "w") as f:
            f.write("#!/usr/bin/env bash\necho old\n")
        r = self._run_write(existing, "#!/usr/bin/env bash\necho new, no pipefail\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_new_sourced_lib_with_no_shebang(self):
        # a sourced lib/env .sh is CONVENTIONALLY shebang-less (it's meant to
        # be `source`d, never executed directly) — `set -euo pipefail` in
        # such a file would leak into the SOURCING shell, which is wrong.
        # Must not be forced to add a shebang it should never have.
        r = self._run_write("lib.sh", "FOO=bar\nexport FOO\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    # --- bypass --------------------------------------------------------

    def test_bypass_inline_marker_allows_and_logs(self):
        import shutil
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        content = ("def f():\n    try:\n        risky()\n"
                   "    except Exception:\n        pass  # airuleset:script-ok legacy probe\n")
        r = self._run_write("bypass.py", content, env_extra={"HOME": home})
        self.assertEqual(r.returncode, 0, r.stdout)
        log = os.path.join(home, "devel", "airuleset", "audits", "script-check-bypasses.log")
        self.assertTrue(os.path.exists(log), "bypass must be logged")
        self.assertIn("legacy probe", open(log).read())

    def test_wired_into_pretooluse_write_and_edit(self):
        cfg = json.loads((airuleset.REPO_DIR / "settings" / "hooks.json").read_text())
        for matcher in ("Write", "Edit"):
            cmds = [h.get("command", "") for blk in cfg["hooks"]["PreToolUse"]
                    if blk.get("matcher") == matcher for h in blk.get("hooks", [])]
            self.assertTrue(any("pre-write-script-check.sh" in c for c in cmds),
                            f"pre-write-script-check.sh not wired to matcher={matcher}")


class TestLocalhostOnGlobeLineHook(TestCase):
    """stop-check-prose-violations.sh (issue #13 sub-item 3, no-localhost-urls.md)
    — a 🌐-marked URL line pointing at localhost/127.0.0.1/0.0.0.0 is a HARD
    block. Scoped ONLY to 🌐 lines (near-zero FP: code/prose discussing
    localhost is untouched)."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"

    def _run(self, msg, session_id=None):
        sid = session_id or f"localhost-test-{uuid.uuid4().hex[:8]}"
        retry_file = f"/tmp/airuleset-stop-block-{sid}"
        if os.path.exists(retry_file):
            os.remove(retry_file)
        self.addCleanup(lambda: os.path.exists(retry_file) and os.remove(retry_file))
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True, timeout=30)

    def test_blocks_globe_line_with_localhost(self):
        r = self._run("Here's the preview: \U0001F310 Dev: http://localhost:5173")
        self.assertIn('"decision": "block"', r.stdout)
        self.assertIn("localhost", r.stdout.lower())

    def test_blocks_globe_line_with_127001(self):
        r = self._run("Check it: \U0001F310 http://127.0.0.1:8080/dashboard")
        self.assertIn('"decision": "block"', r.stdout)

    def test_allows_localhost_in_prose_without_globe(self):
        r = self._run("The dev server runs on localhost:5173 during development.")
        self.assertNotIn('"decision": "block"', r.stdout)

    def test_allows_globe_line_with_real_ip_localhost_elsewhere(self):
        msg = ("For local testing use localhost, but the deployed URL is:\n\n"
              "\U0001F310 Dev: http://100.104.8.125:5173")
        r = self._run(msg)
        self.assertNotIn('"decision": "block"', r.stdout)


class TestLocalhostOnApkLineHook(TestCase):
    """stop-check-prose-violations.sh (#265) — completion-report.md now names
    `\U0001F4F1 <platform>:` (APK/IPA/installable-build) as a sibling artifact
    marker to `\U0001F310`, for client-app projects. no-localhost-urls.md's
    own rule ("This applies to ALL URLs") extends the SAME localhost/
    127.0.0.1/0.0.0.0 ban that already covers \U0001F310 lines to a
    \U0001F4F1-marked line too — one-character widening of the existing
    selector regex, same near-zero-FP shape (scoped ONLY to lines carrying
    the marker)."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"

    def _run(self, msg, session_id=None):
        sid = session_id or f"apk-localhost-test-{uuid.uuid4().hex[:8]}"
        retry_file = f"/tmp/airuleset-stop-block-{sid}"
        if os.path.exists(retry_file):
            os.remove(retry_file)
        self.addCleanup(lambda: os.path.exists(retry_file) and os.remove(retry_file))
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True, timeout=30)

    def test_blocks_apk_line_with_localhost(self):
        r = self._run("Build is ready: \U0001F4F1 APK: http://localhost:8788/app.apk")
        self.assertIn('"decision": "block"', r.stdout)
        self.assertIn("localhost", r.stdout.lower())

    def test_blocks_apk_line_with_127001(self):
        r = self._run("Grab it here: \U0001F4F1 http://127.0.0.1:8788/build.apk")
        self.assertIn('"decision": "block"', r.stdout)

    def test_allows_apk_line_with_real_ip(self):
        msg = ("\U0001F310 Demo: http://100.104.8.125:8080\n"
              "\U0001F4F1 APK: http://100.104.8.125:8788/app-v1.2.3.apk")
        r = self._run(msg)
        self.assertNotIn('"decision": "block"', r.stdout)

    def test_allows_localhost_in_prose_without_apk_marker(self):
        r = self._run("The apk build server runs on localhost:8788 during development.")
        self.assertNotIn('"decision": "block"', r.stdout)


class TestCompletionReportClientAppArtifacts(TestCase):
    """completion-report.md (#265) — the user's complaint (2026-08-06):
    completion reports on a client-app project (a React-Native driver app)
    carried the demo URL on some tickets, the APK URL on exactly one, and
    neither on the rest, because nothing in the module required them
    TOGETHER on every ticket. Locks the broadened wording: the 🌐/📱
    obligation is 'every user-facing artifact this work produced or
    affects' (not just 'one per env × surface'), a client-app project
    needs BOTH 🌐 Demo AND 📱 <platform> on every touching ticket, and a
    still-current artifact URL must be REPEATED, never back-referenced."""

    MODULE = airuleset.REPO_DIR / "modules" / "core" / "completion-report.md"

    def test_module_exists_and_in_profile(self):
        self.assertTrue(self.MODULE.exists())
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        self.assertIn("modules/core/completion-report.md", entries)

    def test_broadens_the_globe_requirement_past_deploy_shaped(self):
        t = self.MODULE.read_text(encoding="utf-8")
        self.assertIn("every user-facing artifact this work produced or affects", t)

    def test_names_both_demo_and_apk_for_client_app_projects(self):
        t = self.MODULE.read_text(encoding="utf-8")
        for phrase in ("client-app project", "\U0001F310 Demo:",
                       "\U0001F4F1 <platform>:", "APK/IPA/signed binary",
                       "every ticket that touched the app"):
            self.assertIn(phrase, t, phrase)

    def test_bans_back_referencing_a_still_current_artifact_url(self):
        t = self.MODULE.read_text(encoding="utf-8")
        self.assertIn("search the transcript for an artifact URL", t)
        self.assertIn("REPEAT it in the report", t)

    def test_template_carries_demo_and_apk_example_lines(self):
        t = self.MODULE.read_text(encoding="utf-8")
        self.assertIn("\U0001F310 Demo: <url>", t)
        self.assertIn("\U0001F4F1 APK:  <url>", t)

    def test_reserves_apk_marker_exclusively_like_globe(self):
        # Adversarial-review finding (#265): the hook's localhost-ban widening
        # claims BOTH markers are used EXCLUSIVELY for a presented artifact
        # URL "per completion-report.md" — that claim was false for 📱 until
        # this bullet existed (📱 could plausibly decorate an unrelated
        # "mobile" sentence). Locks the reservation that makes it true.
        t = self.MODULE.read_text(encoding="utf-8")
        self.assertIn("installable-build DOWNLOAD URL only", t)
        self.assertIn("never a decorative", t)


class TestTesterHandoffHook(TestCase):
    """stop-check-prose-violations.sh (issue #13 sub-item 4) — VERIFIES the
    autonomous-verification.md claim 'The Stop hook blocks them' for banned
    user-as-tester hand-off phrases. This check already existed in the hook
    (untested before this ticket) — these tests lock the claim as TRUE and
    guard the UNVERIFIED: escape hatch."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"

    def _run(self, msg, session_id=None):
        sid = session_id or f"handoff-test-{uuid.uuid4().hex[:8]}"
        retry_file = f"/tmp/airuleset-stop-block-{sid}"
        if os.path.exists(retry_file):
            os.remove(retry_file)
        self.addCleanup(lambda: os.path.exists(retry_file) and os.remove(retry_file))
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True, timeout=30)

    def test_blocks_can_you_test_it(self):
        r = self._run("I fixed the EQ bug. Can you test it on your end and let me know if it works?")
        self.assertIn('"decision": "block"', r.stdout)

    def test_blocks_let_me_know_if_it_works(self):
        r = self._run("Deployed the change. Let me know if it works on your side.")
        self.assertIn('"decision": "block"', r.stdout)

    def test_blocks_please_verify(self):
        r = self._run("The fix is in. Please verify it works in production.")
        self.assertIn('"decision": "block"', r.stdout)

    def test_blocks_next_user_test(self):
        r = self._run("I'll fix locally before the next user test.")
        self.assertIn('"decision": "block"', r.stdout)

    def test_allows_documented_unverified_escape(self):
        msg = ("Deployed the fix. UNVERIFIED: Cannot simulate the claude.ai OAuth flow — "
              "requires the user's authenticated browser session against their actual "
              "claude.ai account. Tool-request asked + rejected.")
        r = self._run(msg)
        self.assertNotIn('"decision": "block"', r.stdout)

    def test_allows_normal_message_with_no_handoff(self):
        r = self._run("Verified via Playwright: clicked the button, confirmed the value updated to 42.")
        self.assertNotIn('"decision": "block"', r.stdout)

    def test_wired_into_stop(self):
        cfg = json.loads((airuleset.REPO_DIR / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "") for blk in cfg["hooks"]["Stop"]
                for h in blk.get("hooks", [])]
        self.assertTrue(any("stop-check-prose-violations.sh" in c for c in cmds))


class TestStatuslineVocabularyModule(TestCase):
    """User ask 2026-07-19: every Claude session must understand that when the
    user says 'issues N', 'gk N' (or 'skipped N') they usually mean the
    STATUSLINE footer segment — the Issues counter airuleset renders at the
    bottom of Claude Code — not a GitHub search to run from scratch."""

    MODULE = airuleset.REPO_DIR / "modules" / "core" / "statusline-vocabulary.md"

    def test_module_exists_and_in_profile(self):
        self.assertTrue(self.MODULE.exists())
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        self.assertIn("modules/core/statusline-vocabulary.md", entries)

    def test_module_explains_every_segment_form(self):
        t = self.MODULE.read_text(encoding="utf-8")
        for phrase in ("Issues N", "gk N", "skipped K", "statusline"):
            self.assertIn(phrase, t, phrase)

    def test_module_no_longer_explains_the_removed_ratio_form(self):
        # #367 (third footer simplification round): the whole `run D/T` /
        # `Issues D/T` active-run-ratio concept is GONE, not just relabelled
        # -- the doc must not still teach a spoken form for a shape the
        # segment can never render any more.
        t = self.MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Issues D/T", t)
        self.assertNotIn("run D/T", t)
        self.assertNotIn("run N/T", t)

    def test_module_documents_the_shortened_render_forms(self):
        # #223 -- every label was abbreviated on the actual footer; the doc
        # must name the CURRENT rendered forms, not just the spoken/historical
        # ones the test above already locks. #367 dropped `run N/T`/
        # `run D/T` (the active-run ratio) and `· gkq N` (duplicate
        # needs-gatekeeper decoration) entirely -- `I N`/`· gk N`/`· skip K`/
        # `Q N` are the ONLY rendered forms left.
        t = self.MODULE.read_text(encoding="utf-8")
        for phrase in ("`I N`", "`· gk N`", "`· skip K`", "`Q N`",
                       "sub <D.M.>"):
            self.assertIn(phrase, t, phrase)

    def test_module_names_the_backing_caches(self):
        # The session should read the SAME local cache the segment renders
        # from (never guess): tickets-status. #367 dropped the segment's
        # own read of autopilot-progress entirely (that cache still exists
        # for job 20, but the footer no longer consults it).
        t = self.MODULE.read_text(encoding="utf-8")
        self.assertIn("tickets-status", t)

    def test_module_no_longer_documents_the_removed_inde_bucket(self):
        # #313 pt 5 removed the cross-project '· inde M' form entirely from
        # the RENDERED forms bullet -- the doc must not claim it still exists.
        t = self.MODULE.read_text(encoding="utf-8")
        self.assertNotIn("`Q N · inde M`", t)
        self.assertNotIn("`Q inde M`", t)

    def test_module_documents_the_width_budget(self):
        # #313 pt 4 -- the doc must explain the new width-budget trim, so a
        # future session reading it understands why a segment can be missing.
        t = self.MODULE.read_text(encoding="utf-8")
        self.assertIn("width", t.lower())
        self.assertIn("◎ /goal", t)


class TestProseHookIgnoresGoalTemplateLines(TestCase):
    """Montalu spin 2026-07-20: the autopilot Step 2 message (banner + printed
    /goal template + the sanctioned 'vlož /goal' question) got hard-blocked as
    a 'pre-implementation pause' — the REVIEW-WATCH clauses added to the /goal
    template ('start…run…immediately…or…check') trip the dispatch-or-hold
    regex, so every /autopilot arm message loops on the Stop hook. /goal
    template lines are sanctioned machinery text — stripped before the
    pre-answered-question checks."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"

    def _run(self, msg):
        sid = f"test-goal-{uuid.uuid4().hex[:12]}"
        self.addCleanup(lambda: Path(
            f"/tmp/airuleset-stop-block-{sid}").unlink(missing_ok=True))
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True)

    def test_autopilot_arm_message_with_goal_template_passes(self):
        goal = (airuleset.REPO_DIR / "skills" / "autopilot" / "SKILL.md").read_text(
            encoding="utf-8")
        import re as _re
        line = _re.findall(r"^/goal STOP CONDITIONS.*$", goal, _re.M)[1]
        msg = ("autopilot · merge=auto · authority=branch-merge · 7 ticketov\n\n"
               "```\n" + line + "\n```\n\n"
               "**Otázka — projekt odoo-erp (Money→Odoo import):** autopilot je "
               "pripravený — backlog má 7 otvorených ticketov.\n"
               "• Vlož /goal riadok vyššie (odporúčam) — loop sa rozbehne a ide sám\n"
               "• Nič nevkladaj — autopilot sa nespustí\n"
               "❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne")
        r = self._run(msg)
        self.assertNotIn("pre-implementation pause", r.stdout,
                         r.stdout + r.stderr)

    def test_real_pre_implementation_pause_still_blocked(self):
        r = self._run("Plan committed. Dispatch the subagents now, "
                      "or hold for your review of the plan first?")
        self.assertIn("pre-implementation pause", r.stdout)


class TestArmQuestionNeverPingsDiscord(TestCase):
    """gk incident 2026-07-20: the '❓ NEEDS YOU: vlož /goal' arm question
    pinged the user's phone although the watchdog auto-arm answers it within a
    minute — and the user's Discord reply ('1') typed into the session could
    not arm anything (only external keystrokes can type /goal). Arm questions
    are MACHINE questions now: the pending hook must not send them."""

    HOOK = airuleset.REPO_DIR / "hooks" / "notify-discord-pending.sh"

    def _run(self, msg):
        sid = f"test-armq-{uuid.uuid4().hex[:12]}"
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid,
                              "cwd": "/tmp"})
        env = dict(os.environ)
        env["HOME"] = tempfile.mkdtemp()
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True, env=env)

    def test_goal_arm_question_is_not_sent(self):
        msg = ("/goal STOP CONDITIONS — the loop is DONE ...\n"
               "**Otázka — projekt odoo-erp (Money→Odoo):** autopilot pripravený.\n"
               "• Vlož /goal riadok vyššie (odporúčam) — rozbehne sa sám\n"
               "❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne")
        r = self._run(msg)
        self.assertIn("arm-question", r.stdout + r.stderr)

    def test_ordinary_question_still_pings(self):
        msg = ("**Otázka — projekt demo (nahrávky):** disk je plný.\n"
               "• Zmazať staré (odporúčam)\n"
               "❓ NEEDS YOU: zmazať nahrávky staršie ako 3 dni?")
        r = self._run(msg)
        self.assertNotIn("arm-question", r.stdout + r.stderr)


class TestQualityGateExemptsArmQuestions(TestCase):
    """gk hook-error loop 2026-07-20: the /goal ARM question is a MACHINE
    question (the watchdog auto-arm answers it; Discord never pings it) — the
    away-user Slovak template gate must not block it; a blocked arm question
    loops the Stop hook and killed the gk session."""

    HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-question-quality.sh"

    def _run(self, msg):
        sid = f"test-qgarm-{uuid.uuid4().hex[:12]}"
        self.addCleanup(lambda: Path(
            f"/tmp/airuleset-question-quality-block-{sid}").unlink(missing_ok=True))
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True)

    def test_bare_arm_question_is_not_blocked(self):
        msg = ("/goal STOP CONDITIONS — ...\n\n"
               "❓ NEEDS YOU: pastni /goal linku vyššie, nech front beží ďalej")
        r = self._run(msg)
        self.assertNotIn('"block"', r.stdout, r.stdout)

    def test_ordinary_bare_question_still_blocked(self):
        r = self._run("❓ NEEDS YOU: schváliš merge PR #5?")
        self.assertIn('"block"', r.stdout)


class TestCiMonitoringPollSnippetSelfBounds(TestCase):
    """#90 (gk subagent, 2026-07-26): the bounded poll loop shape in
    ci-monitoring.md is only safe against the harness's own SIGTERM if the
    Bash tool's `timeout` PARAMETER is raised near its 600000ms cap — easy to
    forget, since the loop body reads as complete without it. Live-hit: a
    poll written exactly per the (old) shape, with no `timeout` raised, was
    SIGTERM'd (exit 143) at the harness's observed 120000ms default, mid-poll,
    with NO output. This EXECUTES the real doc snippet (extracted verbatim
    from the .md, not retyped) to prove it now ends CLEANLY on its own
    `SECONDS`-based budget well before any external kill, regardless of
    whether the tool timeout was ever set."""

    def _extract_snippet(self):
        import re
        text = (airuleset.REPO_DIR / "modules" / "core" / "ci-monitoring.md").read_text()
        m = re.search(r"```bash\n(.*?)\n\s*```", text, re.S)
        self.assertIsNotNone(m, "the DEADLINE= poll snippet must exist in ci-monitoring.md")
        return m.group(1)

    def _run_snippet(self, gh_stub_body, extra_env=None, sleep_interval="0.2"):
        import shutil
        snippet = self._extract_snippet()
        self.assertIn("DEADLINE=", snippet)
        self.assertIn("SECONDS", snippet)
        # Scale the real-time sleep down for a fast test — the ALGORITHM
        # (DEADLINE check via SECONDS, break + message) is untouched; only
        # the numeric wait between polls is substituted so the test doesn't
        # need to burn 30 real seconds per iteration.
        snippet = snippet.replace("sleep 30", f"sleep {sleep_interval}")
        snippet = snippet.replace("<id>", "12345")

        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        binroot = os.path.join(root, "bin")
        os.makedirs(binroot)
        gh_path = os.path.join(binroot, "gh")
        with open(gh_path, "w") as fh:
            fh.write("#!/usr/bin/env bash\n" + gh_stub_body + "\n")
        os.chmod(gh_path, 0o755)

        env = dict(os.environ)
        env["PATH"] = binroot + os.pathsep + env.get("PATH", "")
        if extra_env:
            env.update(extra_env)

        # NOTE: unlike an earlier (pre-#405) version of this method, the
        # `gh_stub_body` each test below passes in is ALREADY the filtered
        # TERMINAL/JOBFAIL/PENDING text -- the stub never prints raw JSON,
        # and jq's OWN `--jq` filter is never actually invoked by this
        # helper (see TestCiMonitoringJqFilterHasRealTeeth below for tests
        # that DO execute the real filter against real jq).
        script = "set -uo pipefail\n" + snippet
        # An external timeout MUCH larger than the loop's own configured
        # budget: if the snippet did NOT self-bound, it would run until this
        # fires (exit 124) instead of exiting cleanly on its own well before it.
        return subprocess.run(["timeout", "20", "bash", "-c", script],
                              capture_output=True, text=True, env=env, cwd=root)

    def test_never_reaches_terminal_exits_on_own_budget_before_external_kill(self):
        # gh always reports "queued" (never completed, never a job failure)
        # -- the stub mimics what `gh ... --jq '<the 3-way TERMINAL/JOBFAIL/
        # PENDING filter>'` itself already prints (gh applies --jq internally;
        # there is no separate `jq` call to fake). The loop must give up on
        # its OWN budget and print the last-known status, never rely on the
        # external `timeout` wrapper to kill it.
        r = self._run_snippet(
            "echo 'PENDING queued'",
            extra_env={"AIRULESET_POLL_BUDGET_S": "1"},
        )
        self.assertNotEqual(r.returncode, 124,
                            "external timeout fired -- snippet did not self-bound: "
                            + r.stdout + r.stderr)
        self.assertIn("POLL BUDGET REACHED", r.stdout, r.stdout + r.stderr)
        self.assertIn("queued", r.stdout, r.stdout + r.stderr)
        self.assertNotIn("TERMINAL", r.stdout)
        self.assertNotIn("JOB FAILED", r.stdout)

    def test_terminal_state_still_breaks_immediately(self):
        # regression guard: a genuinely completed run must still short-circuit
        # via the TERMINAL branch, not wait out the whole budget -- even when
        # a job inside it also failed (the ordinary "run finished, one job
        # red" case), TERMINAL still wins over JOBFAIL (see the jq filter's
        # own if/elif order: .status=="completed" is checked FIRST).
        r = self._run_snippet(
            "echo 'TERMINAL completed success'",
            extra_env={"AIRULESET_POLL_BUDGET_S": "100"},
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TERMINAL: completed success", r.stdout)
        self.assertNotIn("POLL BUDGET REACHED", r.stdout)
        self.assertNotIn("JOB FAILED", r.stdout)

    def test_job_level_failure_wakes_before_run_level_terminal(self):
        # #405: a job inside a STILL-RUNNING multi-job run has already
        # concluded failure -- the waiter must wake immediately with the
        # failed job's name, never wait for the whole run to reach a
        # terminal state (a large budget proves this isn't just the budget
        # expiring), and it must exit cleanly (break, not an external kill).
        r = self._run_snippet(
            "echo 'JOBFAIL E2E Tests (slovnormal)'",
            extra_env={"AIRULESET_POLL_BUDGET_S": "100"},
        )
        self.assertNotEqual(r.returncode, 124,
                            "external timeout fired -- snippet did not wake on "
                            "job failure: " + r.stdout + r.stderr)
        self.assertIn(
            "JOB FAILED (run still in progress): E2E Tests (slovnormal)",
            r.stdout, r.stdout + r.stderr)
        self.assertNotIn("TERMINAL", r.stdout)
        self.assertNotIn("POLL BUDGET REACHED", r.stdout)

    def test_default_budget_is_under_the_observed_harness_timeout(self):
        # The DEFAULT literal (no AIRULESET_POLL_BUDGET_S set) must stay
        # safely under the harness's OBSERVED default tool-call timeout
        # (120s) -- a static check on the real snippet text, not a full
        # ~100s real-time execution (the runtime behavior itself is already
        # proven above with an explicit override). This is the whole point
        # of #90: a model that forgets to raise the Bash tool `timeout`
        # parameter still gets a clean "not yet terminal" message before the
        # observed 120s default SIGTERMs it, instead of a silent kill.
        import re
        snippet = self._extract_snippet()
        m = re.search(r"AIRULESET_POLL_BUDGET_S:-(\d+)\}", snippet)
        self.assertIsNotNone(m, "no default literal found: " + snippet)
        default_budget = int(m.group(1))
        self.assertLess(default_budget, 120,
                        "default poll budget (%ds) is not safely under the "
                        "harness's observed 120s default timeout" % default_budget)


class TestCiMonitoringJqFilterHasRealTeeth(TestCase):
    """#405 adversarial-review MINOR-1: every test in the sibling class above
    stubs `gh` to echo the ALREADY-FILTERED text (TERMINAL/JOBFAIL/PENDING)
    directly -- the real `--jq` filter text in ci-monitoring.md is never
    actually executed by jq anywhere in the suite, for EITHER shape (the
    foreground loop, single-quoted `--jq '...'`, or the background waiter,
    double-quoted `--jq "..."` nested inside a `bash -c '...'`). A
    syntactically broken filter, or a swapped if/elif branch order, would
    ship green.

    This class extracts the REAL, bash-quote-RESOLVED `--jq` argument value
    for each shape (a stub `gh` on PATH captures whatever bash actually
    handed it as argv, sidestepping any need to hand-parse the doc's own
    quoting) and feeds it to a REAL `jq` binary against crafted JSON
    payloads via stdin -- proving both branch selection AND branch order.
    """

    def _extract_block(self, index):
        # ci-monitoring.md has exactly two fenced blocks: the first tagged
        # ```bash (the foreground loop), the second bare ``` (the
        # background waiter). Splitting on the literal fence marker avoids
        # the "bare fence" ambiguity a single non-greedy regex would hit
        # (the FIRST block's own CLOSING fence is bare too).
        text = (airuleset.REPO_DIR / "modules" / "core" / "ci-monitoring.md").read_text()
        parts = text.split("```")
        self.assertEqual(len(parts), 5,
                          "expected exactly two fenced blocks in ci-monitoring.md")
        code = parts[1 + index * 2]
        if code.startswith("bash\n"):
            code = code[len("bash\n"):]
        return code.strip("\n")

    def _extract_real_jq_filter(self, snippet):
        """Runs `snippet` with a stub `gh` on PATH that captures the REAL
        argv value bash resolved for --jq (whatever quoting the doc used),
        then returns that captured text -- the exact bytes a real `gh`
        would hand to its own internal jq evaluator."""
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        binroot = os.path.join(root, "bin")
        os.makedirs(binroot)
        capture_path = os.path.join(root, "captured-jq-filter")
        gh_path = os.path.join(binroot, "gh")
        with open(gh_path, "w") as fh:
            fh.write(
                "#!/usr/bin/env bash\n"
                "prev=\"\"\n"
                "for a in \"$@\"; do\n"
                "  if [ \"$prev\" = \"--jq\" ]; then\n"
                "    printf '%s' \"$a\" > \"$CAPTURE_FILE\"\n"
                "  fi\n"
                "  prev=\"$a\"\n"
                "done\n"
                "echo 'TERMINAL completed success'\n")
        os.chmod(gh_path, 0o755)

        snippet = snippet.replace("<id>", "12345")
        env = dict(os.environ)
        env["PATH"] = binroot + os.pathsep + env.get("PATH", "")
        env["CAPTURE_FILE"] = capture_path
        r = subprocess.run(
            ["timeout", "20", "bash", "-c", "set -uo pipefail\n" + snippet],
            capture_output=True, text=True, env=env, cwd=root)
        self.assertNotEqual(r.returncode, 124,
                             "snippet did not terminate on the stub's "
                             "TERMINAL response: " + r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(capture_path),
                         "stub gh never saw a --jq flag at all: "
                         + r.stdout + r.stderr)
        with open(capture_path) as fh:
            return fh.read()

    def _run_jq(self, filter_text, json_payload):
        # `-r` (raw output, no surrounding quotes on a string result) --
        # confirmed live against a real GitHub Actions run that `gh`'s own
        # `--jq` flag behaves this way by default (`gh run view <id> --json
        # status,conclusion --jq '.status+" "+(.conclusion//"")'` prints
        # `completed success`, unquoted); the bash `case "$s" in "TERMINAL
        # "*)` match in the shipped snippet only works against unquoted
        # output, so this must mirror `gh`'s real behavior, not bare `jq`'s.
        r = subprocess.run(["jq", "-r", filter_text], input=json_payload,
                            capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0,
                          "jq failed to even parse/run the filter -- "
                          "filter: %r\nstderr: %s" % (filter_text, r.stderr))
        return r.stdout.strip()

    # ---- shared branch-correctness cases, run against BOTH shapes --------
    CASES = [
        ('{"status":"queued","jobs":[]}', "PENDING queued"),
        ('{"status":"in_progress","jobs":null}', "PENDING in_progress"),
        (
            '{"status":"in_progress","jobs":[{"name":"E2E Tests (slovnormal)",'
            '"conclusion":"failure"},{"name":"Lint","conclusion":"success"}]}',
            'JOBFAIL E2E Tests (slovnormal)',
        ),
        (
            '{"status":"in_progress","jobs":[{"name":"A","conclusion":"failure"},'
            '{"name":"B","conclusion":"failure"}]}',
            "JOBFAIL A, B",
        ),
        (
            # a run that legitimately COMPLETES with a failed job -- TERMINAL
            # must win (checked FIRST in the if/elif), never JOBFAIL, so the
            # if/elif ORDER is what this case actually locks.
            '{"status":"completed","conclusion":"failure","jobs":'
            '[{"name":"E2E","conclusion":"failure"}]}',
            "TERMINAL completed failure",
        ),
        (
            '{"status":"completed","conclusion":"success","jobs":'
            '[{"name":"Lint","conclusion":"success"}]}',
            "TERMINAL completed success",
        ),
        (
            # #405 adversarial-review MINOR-4: a job hitting its own
            # `timeout-minutes` is arguably the LIKELIEST real way the
            # ticket's own scenario (a long-running multi-job matrix, one
            # job in trouble while the run stays in_progress) materializes
            # -- must fail-fast exactly like a hard `failure`, never sit
            # invisible as PENDING for the rest of the run.
            '{"status":"in_progress","jobs":[{"name":"Slow Job",'
            '"conclusion":"timed_out"},{"name":"Lint","conclusion":"success"}]}',
            "JOBFAIL Slow Job",
        ),
        (
            # a CANCELLED job must NOT fail-fast -- either it's a cascade
            # from a sibling `failure` (which that sibling's own conclusion
            # already reports), or the whole run was deliberately cancelled
            # by a human (`gh run cancel`), in which case "JOB FAILED" would
            # be an actively misleading wake for a choice, not a defect.
            '{"status":"in_progress","jobs":[{"name":"Dependent",'
            '"conclusion":"cancelled"},{"name":"Lint","conclusion":"success"}]}',
            "PENDING in_progress",
        ),
    ]

    def test_foreground_loop_jq_filter(self):
        snippet = self._extract_block(0)
        self.assertIn("--jq", snippet)
        filt = self._extract_real_jq_filter(snippet)
        for payload, expected in self.CASES:
            self.assertEqual(self._run_jq(filt, payload), expected,
                              "payload: " + payload)

    def test_background_waiter_jq_filter(self):
        snippet = self._extract_block(1)
        self.assertIn("--jq", snippet)
        filt = self._extract_real_jq_filter(snippet)
        for payload, expected in self.CASES:
            self.assertEqual(self._run_jq(filt, payload), expected,
                              "payload: " + payload)


class TestNudgePollLoopTimeoutHook(TestCase):
    """#90: a PreToolUse(Bash) NUDGE (never a block -- the poll must always
    pass) reminding the model to raise the Bash tool's own `timeout`
    parameter whenever it is about to run a bounded sleep/poll loop without
    one -- the mechanical half of #90, since prose alone ("set the timeout
    near its cap") is easy to forget and #14 already showed hooks can only
    ever act on the assistant's own OUTPUT/COMMAND, never on module prose."""

    HOOK = "nudge-poll-loop-timeout.sh"

    def _run(self, command, timeout=None):
        tool_input = {"command": command}
        if timeout is not None:
            tool_input["timeout"] = timeout
        payload = json.dumps({"tool_input": tool_input})
        return subprocess.run(
            ["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
            input=payload, text=True, capture_output=True, timeout=30)

    def test_poll_loop_with_no_timeout_param_is_nudged(self):
        r = self._run("for i in $(seq 1 18); do gh run view 1 "
                       "--json status; sleep 30; done")
        self.assertEqual(r.returncode, 0, "must NEVER block: " + r.stdout + r.stderr)
        self.assertIn("timeout", r.stdout + r.stderr)

    def test_poll_loop_with_a_low_timeout_param_is_nudged(self):
        r = self._run("for i in $(seq 1 18); do gh run view 1 "
                       "--json status; sleep 30; done", timeout=60000)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("timeout", r.stdout + r.stderr)

    def test_poll_loop_with_a_raised_timeout_param_is_silent(self):
        r = self._run("for i in $(seq 1 18); do gh run view 1 "
                       "--json status; sleep 30; done", timeout=580000)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NUDGE", r.stdout + r.stderr)

    def test_ordinary_short_command_is_never_nudged(self):
        r = self._run("gh pr view 42")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NUDGE", r.stdout + r.stderr)

    def test_non_loop_command_with_sleep_alone_is_not_nudged(self):
        # `sleep` with no loop-body `done` closer is not a poll shape -- a
        # one-shot `sleep 5 && curl ...` is a normal short wait, not the
        # multi-minute pattern this hook targets.
        r = self._run("sleep 5 && curl -s http://example.com")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NUDGE", r.stdout + r.stderr)

    def test_heredoc_that_merely_mentions_a_poll_loop_is_not_nudged(self):
        # #111: the detector used two INDEPENDENT token greps (`sleep`
        # anywhere AND `done` anywhere), so a command that merely WRITES
        # ABOUT a poll loop -- documentation, a note, a rule file whose
        # whole subject IS this shape -- took the full ~150-token nudge.
        # Writing prose is not polling.
        r = self._run("cat >> notes.md <<'EOF'\n"
                      "the loop does: sleep 30; done\n"
                      "EOF")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NUDGE", r.stdout + r.stderr)

    def test_grepping_the_poll_loop_doc_is_not_nudged(self):
        # #111: grepping ci-monitoring.md -- the very rule that documents
        # the poll shape -- carries both tokens as SEARCH TEXT and nudged.
        # This fired repeatedly during #110 purely because that ticket
        # edited and grepped this file.
        r = self._run("grep -n 'sleep 30' modules/core/ci-monitoring.md "
                      "| grep done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NUDGE", r.stdout + r.stderr)

    def test_settle_sleep_before_an_unrelated_loop_is_not_nudged(self):
        # #111: `sleep` and `done` present but in unrelated statements --
        # a one-shot settle, then a fan-out loop with no wait in its body.
        # Nothing here polls; the call is over in seconds.
        r = self._run("sleep 10 && for h in a b; do curl -s $h; done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NUDGE", r.stdout + r.stderr)

    def test_nested_poll_loop_is_still_nudged(self):
        # #111 guard on the OTHER direction: a real retry poll whose body
        # holds an inner loop must KEEP nudging. Pins the rejected
        # "sleep must precede the NEXT done" containment variant, which
        # silently stopped nudging this shape (a real 135s poll in the
        # corpus) -- tightening must not buy quiet by dropping true
        # positives.
        r = self._run("for t in 1 2 3; do for i in 1 2; do "
                      "ssh h$i systemctl is-active x; done; sleep 20; done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("timeout", r.stdout + r.stderr)

    def test_tight_do_sleep_body_is_still_nudged(self):
        # #111 boundary-SHARING guard: `do sleep 5` has exactly ONE space
        # between the two tokens, so a detector built from CONSUMING
        # character classes cannot match it (the space would have to be
        # both `do`'s closing boundary and `sleep`'s opening one). Every
        # other loop test here happens to put a command between `do` and
        # `sleep`, which hid that from the unit tests entirely -- the
        # corpus replay caught it going quiet on 243 real polls.
        r = self._run("until [ -s /tmp/out ]; do sleep 5; done; cat /tmp/out")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("timeout", r.stdout + r.stderr)

    def test_backgrounded_poll_loop_is_not_nudged(self):
        # #107: ci-monitoring.md now sanctions ONE `run_in_background: true`
        # waiter for a LONG wait -- it detaches immediately, so the Bash
        # tool's own `timeout` parameter (which only bounds the FOREGROUND
        # call) is irrelevant to it. Nudging "raise your timeout near
        # 600000ms" on a backgrounded loop is wrong advice; only a
        # FOREGROUND sleep/poll loop needs that nudge.
        tool_input = {
            "command": "while :; do gh run view 1 --json status; "
                       "sleep 60; done",
            "run_in_background": True,
        }
        payload = json.dumps({"tool_input": tool_input})
        r = subprocess.run(
            ["bash", str(airuleset.REPO_DIR / "hooks" / self.HOOK)],
            input=payload, text=True, capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NUDGE", r.stdout + r.stderr)

    def test_wired_into_pretooluse_bash(self):
        cfg = json.loads((airuleset.REPO_DIR / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "") for blk in cfg["hooks"]["PreToolUse"]
                if blk.get("matcher") == "Bash" for h in blk.get("hooks", [])]
        self.assertTrue(any("nudge-poll-loop-timeout.sh" in c for c in cmds))
