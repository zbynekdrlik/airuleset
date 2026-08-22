"""Behaviour tests for hooks/inject-situational-rule.sh (#91).

Background: every module->skill "conversion" so far was a silent DELETE in
effect. Measured across 91 transcripts: 25 of 34 skills had ZERO lifetime
invocations, 0 of 37 real `gh pr merge` transcripts ever loaded the
`pr-merge-policy` skill, 0 of 46 CI-polling transcripts ever loaded
`ci-monitor`. A skill body enters context ONLY when the model volunteers a
`Skill` call, and a three-line pointer stub demonstrably does not make it do
that.

Two surfaces were PROVEN (live, isolated profile, CC 2.1.220) to load without
the model volunteering anything:

  * `rules/*.md` + `paths:` frontmatter -> injected as a `nested_memory`
    attachment the moment a matching file is touched. Good for content bound
    to a FILE TYPE.
  * a `PreToolUse` hook returning
    `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
      "additionalContext": ...}}` -> injected before the tool runs. This is
    the only automatic surface for content bound to an ACTION
    (`gh pr merge`, `git push`, a deploy, an Agent dispatch).

This file locks the second surface: the trigger table, the hook that reads it,
its once-per-session dedup, and the wiring.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "inject-situational-rule.sh"
CONF = ROOT / "hooks" / "situational-triggers.conf"


def load_conf():
    """Parse the trigger table -> list of (topic, tool, pattern, body)."""
    rows = []
    for line in CONF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        parts = [p for p in parts if p != ""]
        # a 5th column (an exclude ERE that vetoes a match) is optional
        assert len(parts) in (4, 5), f"malformed trigger row: {line!r}"
        rows.append(tuple(parts[:4]))
    return rows


def run(tool_input, tool_name="Bash", session_id="sess-A", tmpdir=None):
    payload = json.dumps(
        {"session_id": session_id, "tool_name": tool_name, "tool_input": tool_input}
    )
    env = dict(os.environ)
    if tmpdir:
        env["TMPDIR"] = tmpdir
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True, env=env
    )


def injected(result):
    """Return the additionalContext string, or None when nothing was injected."""
    out = result.stdout.strip()
    if not out:
        return None
    data = json.loads(out)
    spec = data["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse", spec
    return spec["additionalContext"]


class TestTriggerTable(TestCase):
    def test_conf_exists_and_parses(self):
        self.assertTrue(CONF.exists(), f"missing trigger table {CONF}")
        self.assertTrue(load_conf(), "trigger table is empty")

    def test_every_body_file_exists(self):
        for topic, _tool, _pat, body in load_conf():
            self.assertTrue(
                (ROOT / body).exists(), f"{topic}: body file {body} does not exist"
            )

    def test_topics_are_unique(self):
        topics = [r[0] for r in load_conf()]
        self.assertEqual(len(topics), len(set(topics)), "duplicate topic in table")

    def test_every_stub_target_skill_has_a_trigger(self):
        """The whole point of #91: a converted skill must have a load path.

        Every skill that is the target of a pointer stub in modules/ must be
        reachable without the model volunteering a `Skill` call. rules/+paths:
        is not an option for the write-shaped ones, because that surface fires
        on Read only — never on Edit/Write.
        """
        bodies = {r[3] for r in load_conf()}
        for skill in [
            "pr-merge-policy",
            "ci-push-discipline",
            "ci-monitor",
            "post-deploy-verification",
            "deploy-ssh",
            "local-builds",
            "mutation-testing",
            "verify-issue-still-valid",
            "batch-issue-development",
            "regression-test-first",
            "verify-launched-work-liveness",
            "subagent-type-discipline",
            "investigate-existing-first",
            "windows-remote-gui",
            "comprehensive-logging",
            "version-on-dashboard",
            "deliver-files-as-urls",
            "view-image-urls",
            "notification-mechanics",
        ]:
            self.assertIn(
                f"skills/{skill}/SKILL.md",
                bodies,
                f"{skill} has no automatic load trigger — it would stay silently deleted",
            )


class TestInjection(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_gh_pr_merge_injects_the_merge_policy_body(self):
        ctx = injected(run({"command": "gh pr merge 5 --merge"}, tmpdir=self.tmpdir))
        self.assertIsNotNone(ctx, "gh pr merge must load the merge policy")
        # a string that exists ONLY in the skill body, in no always-on module
        self.assertIn("airuleset:autopilot=auto-merge", ctx)
        self.assertIn("pr-merge-policy", ctx)

    def test_fable_gate_injects_the_tier_reference(self):
        # #92 item 2 moved the tier/pricing/policy-history layer OUT of the
        # always-on model-awareness module and INTO the fable-advisor skill.
        # Running the budget gate IS the action that means "I am deciding a
        # tier right now" — the moved content must load on it, or the move
        # repeats the silent-delete failure #91 exists to prevent.
        ctx = injected(run({"command": "python3 ~/devel/airuleset/airuleset.py fable-gate"},
                           tmpdir=self.tmpdir))
        self.assertIsNotNone(ctx, "fable-gate must load the tier reference")
        # strings that now exist ONLY in the skill body, in no always-on module
        self.assertIn("Fable 5 $10/$50", ctx)
        self.assertIn("Dormant — the Fable-everywhere MAX-PERFORMANCE mode", ctx)

    def test_unrelated_command_injects_nothing(self):
        r = run({"command": "ls -la && echo hello"}, tmpdir=self.tmpdir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_output_is_valid_json_for_the_hook_contract(self):
        r = run({"command": "git push origin dev"}, tmpdir=self.tmpdir)
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(
            data["hookSpecificOutput"]["hookEventName"], "PreToolUse"
        )
        self.assertIsInstance(
            data["hookSpecificOutput"]["additionalContext"], str
        )

    def test_frontmatter_is_stripped_from_the_injected_body(self):
        ctx = injected(run({"command": "gh pr merge 7"}, tmpdir=self.tmpdir))
        self.assertNotIn("user-invocable:", ctx)
        self.assertNotIn("\nname: pr-merge-policy", ctx)

    def test_injected_body_is_labelled_as_an_auto_loaded_project_rule(self):
        """Unlabelled injected text reads as a prompt-injection attempt.

        Observed live: a bare injected instruction was refused by the model as
        "prompt injection, not obeying it". The wrapper is what makes it land
        as a project rule.
        """
        ctx = injected(run({"command": "gh pr merge 7"}, tmpdir=self.tmpdir))
        self.assertIn("airuleset", ctx.lower())
        self.assertIn("project rule", ctx.lower())

    def test_second_call_in_the_same_session_does_not_re_inject(self):
        first = injected(run({"command": "gh pr merge 5"}, tmpdir=self.tmpdir))
        self.assertIsNotNone(first)
        second = run({"command": "gh pr merge 6 --merge"}, tmpdir=self.tmpdir)
        self.assertEqual(
            second.stdout.strip(), "", "body must be paid for once per session"
        )

    def test_a_different_session_gets_its_own_injection(self):
        injected(run({"command": "gh pr merge 5"}, session_id="s1", tmpdir=self.tmpdir))
        again = injected(
            run({"command": "gh pr merge 5"}, session_id="s2", tmpdir=self.tmpdir)
        )
        self.assertIsNotNone(again, "a fresh session must get the rule too")

    def test_two_different_topics_both_inject_in_one_session(self):
        a = injected(run({"command": "gh pr merge 5"}, tmpdir=self.tmpdir))
        b = injected(run({"command": "git push origin dev"}, tmpdir=self.tmpdir))
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertNotEqual(a, b)

    def test_tool_scoping_an_agent_rule_does_not_fire_on_bash(self):
        """subagent-type-discipline is wired to the Agent tool, not to Bash."""
        ctx = injected(
            run({"command": "echo subagent_type general-purpose"}, tmpdir=self.tmpdir)
        )
        if ctx is not None:
            self.assertNotIn("subagent-type-discipline", ctx)

    def test_agent_dispatch_injects_the_subagent_type_rule(self):
        ctx = injected(
            run(
                {"subagent_type": "general-purpose", "prompt": "do a thing"},
                tool_name="Agent",
                tmpdir=self.tmpdir,
            )
        )
        self.assertIsNotNone(ctx, "an Agent dispatch must load the subagent-type rule")
        self.assertIn("subagent_type", ctx)

    def test_background_bash_injects_the_liveness_rule(self):
        ctx = injected(
            run(
                {"command": "python3 long_job.py", "run_in_background": True},
                tmpdir=self.tmpdir,
            )
        )
        self.assertIsNotNone(ctx, "a background launch must load the liveness rule")
        self.assertIn("liveness", ctx.lower())

    def test_missing_session_id_still_works(self):
        payload = json.dumps({"tool_input": {"command": "gh pr merge 5"}})
        env = dict(os.environ, TMPDIR=self.tmpdir)
        r = subprocess.run(
            ["bash", str(HOOK)], input=payload, capture_output=True, text=True, env=env
        )
        self.assertEqual(r.returncode, 0)

    def test_malformed_payload_fails_open(self):
        env = dict(os.environ, TMPDIR=self.tmpdir)
        r = subprocess.run(
            ["bash", str(HOOK)], input="not json at all", capture_output=True,
            text=True, env=env,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_never_blocks_the_tool_call(self):
        """This hook injects context; it must never deny a command."""
        for cmd in ["gh pr merge 5", "git push", "rm -rf /tmp/x", "ls"]:
            r = run({"command": cmd}, session_id=f"s-{cmd}", tmpdir=self.tmpdir)
            self.assertEqual(r.returncode, 0, f"must not block: {cmd}")


class TestFalsePositives(TestCase):
    """A command that merely MENTIONS a trigger must not load anything.

    Live incident during #91 itself: a `gh issue comment -F body.md` whose
    heredoc body described the trigger table matched nine topics at once and
    injected 65.3 KB into the session. Same class as #80 — classify the
    command, never the document it carries.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_heredoc_body_mentioning_triggers_injects_nothing(self):
        cmd = (
            "cat > body.md <<'EOF'\n"
            "The table binds `gh pr merge` and `git push` and `gh run list`\n"
            "and `rsync` and `cargo mutants` to their rule bodies.\n"
            "EOF\n"
            "gh issue comment 91 -F body.md"
        )
        r = run({"command": cmd}, tmpdir=self.tmpdir)
        self.assertEqual(
            r.stdout.strip(), "", "a heredoc document is not an action"
        )

    def test_commit_message_mentioning_a_trigger_injects_nothing(self):
        r = run(
            {"command": 'git commit -m "docs: explain gh pr merge gating"'},
            tmpdir=self.tmpdir,
        )
        ctx = injected(r)
        if ctx is not None:
            self.assertNotIn("pr-merge-policy", ctx)

    def test_echoing_a_trigger_string_injects_nothing(self):
        r = run({"command": "echo 'gh pr merge 5 --merge'"}, tmpdir=self.tmpdir)
        self.assertEqual(r.stdout.strip(), "")

    def test_editing_a_test_file_does_not_load_the_logging_rule(self):
        """comprehensive-logging governs feature code, not the test suite."""
        for path in [
            "/repo/tests/test_thing.py",
            "/repo/src/foo.test.ts",
            "/repo/spec/bar_spec.rb",
        ]:
            ctx = injected(
                run(
                    {"file_path": path, "old_string": "a", "new_string": "b"},
                    tool_name="Edit",
                    session_id="t-" + path,
                    tmpdir=self.tmpdir,
                )
            )
            if ctx is not None:
                self.assertNotIn("comprehensive-logging", ctx, path)

    def test_editing_feature_code_does_load_the_logging_rule(self):
        ctx = injected(
            run(
                {"file_path": "/repo/src/service.py", "old_string": "a",
                 "new_string": "b"},
                tool_name="Edit",
                tmpdir=self.tmpdir,
            )
        )
        self.assertIsNotNone(ctx)
        self.assertIn("comprehensive-logging", ctx)

    def test_a_real_action_still_injects(self):
        ctx = injected(run({"command": "gh pr merge 5 --merge"}, tmpdir=self.tmpdir))
        self.assertIsNotNone(ctx, "the real action must still load the rule")
        self.assertIn("pr-merge-policy", ctx)

    def test_one_call_never_injects_an_unbounded_pile(self):
        cmd = (
            "gh pr merge 5 && git push && gh run list && rsync -a a b && "
            "cargo mutants && gh issue view 1 && gh issue list && "
            "cargo build && stryker run"
        )
        ctx = injected(run({"command": cmd}, tmpdir=self.tmpdir))
        if ctx is not None:
            self.assertLess(
                len(ctx), 20000, "one tool call must not dump a rule pile into context"
            )

    def test_unconsumed_topics_stay_available_for_their_own_action(self):
        """A topic skipped by the size cap must not be silently marked used."""
        cmd = (
            "gh pr merge 5 && git push && gh run list && rsync -a a b && "
            "cargo mutants && gh issue view 1 && gh issue list && "
            "cargo build && stryker run"
        )
        run({"command": cmd}, session_id="cap", tmpdir=self.tmpdir)
        # whatever was dropped must still be loadable later; at least one of the
        # later actions must still produce content in this same session
        later = [
            injected(run({"command": c}, session_id="cap", tmpdir=self.tmpdir))
            for c in ["stryker run", "cargo build --release", "gh issue list"]
        ]
        self.assertTrue(
            any(x is not None for x in later),
            "capping must defer topics, not consume them",
        )


class TestItem95_Item12NewSkillTriggers(TestCase):
    """#95 item 12 (2026-08-09): `fast-iterate`, `meeting-analysis` and
    `playbook-cleanup` had ZERO injection AND zero Skill-tool invocations in
    the 7-day 2026-08-08 measurement (issue #95 comment 5223460819), because
    none of the three had a trigger row in the table at all. User decision
    (comment 5230729320): add trigger rows for these three real skills
    only -- nothing withdrawn, and re-measure in a few weeks whether the
    trigger alone is enough to make each skill actually get delivered.

    Each trigger is picked from the skill's OWN "when to use" description,
    not guessed: fast-iterate binds to the same heavy-local-build Bash
    shape `local-builds` already fires on (the exact moment its own
    SKILL.md says Tier-2 switching becomes actionable); meeting-analysis
    and playbook-cleanup bind to UserPromptSubmit, since both are
    inherently user-request-driven with no Bash action of their own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _prompt(self, text, session_id="sess-item12"):
        payload = json.dumps(
            {
                "session_id": session_id,
                "hook_event_name": "UserPromptSubmit",
                "prompt": text,
            }
        )
        env = dict(os.environ, TMPDIR=self.tmpdir)
        return subprocess.run(
            ["bash", str(HOOK)], input=payload, capture_output=True, text=True, env=env
        )

    def _injected_prompt(self, result):
        """Same shape as the module-level `injected()`, but for the
        UserPromptSubmit surface, whose `hookEventName` is "UserPromptSubmit"
        -- `injected()` hard-asserts "PreToolUse" and would fail here for
        the wrong reason."""
        out = result.stdout.strip()
        if not out:
            return None
        data = json.loads(out)
        spec = data["hookSpecificOutput"]
        assert spec["hookEventName"] == "UserPromptSubmit", spec
        return spec["additionalContext"]

    def test_all_three_topics_have_a_trigger_row(self):
        bodies = {r[3] for r in load_conf()}
        for skill in ["fast-iterate", "meeting-analysis", "playbook-cleanup"]:
            self.assertIn(
                f"skills/{skill}/SKILL.md",
                bodies,
                f"{skill} has no automatic load trigger — #95 item 12",
            )

    def test_heavy_local_build_injects_fast_iterate(self):
        # local-builds shares the SAME trigger pattern (a real, existing
        # precedent — deploy-ssh + post-deploy-verification already share
        # one trigger too) and its own body alone (9662 chars) plus
        # fast-iterate's (5825 chars) exceed the per-call MAX_TOTAL budget
        # (14000), so the FIRST matching call in a session only delivers
        # local-builds — fast-iterate's marker stays UNSET (deferred, per
        # the injector's own documented "defer, never drop" contract) and
        # fires on the session's NEXT matching action, once local-builds'
        # own marker is already consumed and stops competing for budget.
        first = injected(run({"command": "cargo build --release"}, tmpdir=self.tmpdir))
        self.assertIsNotNone(first, "a heavy local build must load local-builds")
        second = injected(
            run({"command": "cargo build --release"}, tmpdir=self.tmpdir)
        )
        self.assertIsNotNone(
            second, "fast-iterate must fire on the session's next heavy build"
        )
        # a string that exists ONLY in fast-iterate's own body
        self.assertIn("Toggle Tier-2 Local Build Mode", second)

    def test_meeting_recording_prompt_injects_meeting_analysis(self):
        r = self._prompt(
            "Mam nahravku zo stretnutia so zakaznikom, analyzuj ju prosim."
        )
        ctx = self._injected_prompt(r)
        self.assertIsNotNone(ctx, "a meeting-recording prompt must load meeting-analysis")
        self.assertIn("Multimodal Meeting Analysis", ctx)

    def test_playbook_cleanup_request_injects_the_skill(self):
        r = self._prompt(
            "Potrebujem spravit playbook cleanup pre tento projekt, vela je "
            "rozhadzane po memory a CLAUDE.md."
        )
        ctx = self._injected_prompt(r)
        self.assertIsNotNone(ctx, "an explicit playbook-cleanup request must load the skill")
        self.assertIn("Playbook Cleanup", ctx)

    def test_unrelated_prompt_injects_neither_new_userpromptsubmit_skill(self):
        r = self._prompt("Ako sa mas, aky je dnes den?")
        ctx = self._injected_prompt(r)
        if ctx is not None:
            self.assertNotIn("Multimodal Meeting Analysis", ctx)
            self.assertNotIn("Playbook Cleanup", ctx)


class TestItem95_RootCauseNewSkillTriggers(TestCase):
    """#95 item 12 root-cause re-measure (2026-08-15). The 2026-08-15 injection
    re-measure (issue #95, SAME yardstick as comment 5223460819) found three
    MODEL-VISIBLE skills at ZERO reach on this box with NO trigger row at all --
    the exact gap the first item-12 pass closed for fast-iterate/meeting-analysis/
    playbook-cleanup, reopened by three skills added AFTER that pass:
    cloudflare-api-tokens, claude-code-log (#420) and odoo-discuss-xmlrpc (#465).
    None is a delete candidate (each has a validated dormancy reason); the
    FREEZE-safe fix is the SAME as item 12 -- a real action trigger from each
    skill's OWN 'when to use' text.

    Each pattern is validated against `inject-situational-rule.sh`'s own
    quote-stripping AND against real false positives (adversarial review, this
    pass): cloudflare's reliable Bash catch is the UNQUOTED `api.cloudflare.com`
    / `wrangler` (a quoted URL is stripped); odoo binds to Write|Edit ONLY (the
    `message_post` token in the CONTENT, never stripped for a non-Bash tool — a
    Bash surface caught `grep message_post` far more than any real post); and
    claude-code-log's `session` arms REQUIRE an html/markdown format so they no
    longer fire on "export a session cookie" / "browse the session logs"."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _prompt(self, text, session_id="sess-rootcause"):
        payload = json.dumps(
            {
                "session_id": session_id,
                "hook_event_name": "UserPromptSubmit",
                "prompt": text,
            }
        )
        env = dict(os.environ, TMPDIR=self.tmpdir)
        return subprocess.run(
            ["bash", str(HOOK)], input=payload, capture_output=True, text=True, env=env
        )

    def _injected_prompt(self, result):
        out = result.stdout.strip()
        if not out:
            return None
        data = json.loads(out)
        spec = data["hookSpecificOutput"]
        assert spec["hookEventName"] == "UserPromptSubmit", spec
        return spec["additionalContext"]

    def test_all_three_new_topics_have_a_trigger_row(self):
        bodies = {r[3] for r in load_conf()}
        for skill in ["cloudflare-api-tokens", "claude-code-log", "odoo-discuss-xmlrpc"]:
            self.assertIn(
                f"skills/{skill}/SKILL.md",
                bodies,
                f"{skill} has no automatic load trigger — #95 item 12 root-cause pass",
            )

    def test_cloudflare_api_call_injects_the_skill(self):
        ctx = injected(
            run({"command": "curl https://api.cloudflare.com/client/v4/zones"},
                tmpdir=self.tmpdir)
        )
        self.assertIsNotNone(ctx, "a Cloudflare API call must load the token skill")
        # a string that exists ONLY in cloudflare-api-tokens' own body
        self.assertIn("Using Cloudflare API Tokens", ctx)

    def test_wrangler_command_injects_the_cloudflare_skill(self):
        ctx = injected(run({"command": "wrangler deploy"}, tmpdir=self.tmpdir))
        self.assertIsNotNone(ctx, "the Cloudflare CLI must load the token skill")
        self.assertIn("Using Cloudflare API Tokens", ctx)

    def test_odoo_message_post_write_injects_the_skill(self):
        ctx = injected(
            run(
                {"file_path": "/repo/importer.py",
                 "content": "channel.message_post(body=html, body_is_html=True)"},
                tool_name="Write",
                tmpdir=self.tmpdir,
            )
        )
        self.assertIsNotNone(ctx, "writing a message_post call must load the odoo recipe")
        # a string that exists ONLY in odoo-discuss-xmlrpc' own body
        self.assertIn("Odoo Discuss over XML-RPC", ctx)

    def test_claude_code_log_export_prompt_injects_the_skill(self):
        r = self._prompt("Please export this session as HTML so I can share it")
        ctx = self._injected_prompt(r)
        self.assertIsNotNone(ctx, "a transcript-export request must load claude-code-log")
        # a string that exists ONLY in claude-code-log's own body (not the
        # injection wrapper, which always carries the topic name) -- an
        # adversarial-review finding: asserting "claude-code-log" alone is a
        # tautology satisfied by source="airuleset:claude-code-log".
        self.assertIn("Browsing / Exporting / Archiving Transcripts", ctx)

    def test_claude_code_log_slovak_prompt_injects_the_skill(self):
        r = self._prompt("exportuj tento session do html prosim")
        ctx = self._injected_prompt(r)
        self.assertIsNotNone(ctx, "a Slovak transcript-export request must load the skill")
        self.assertIn("Browsing / Exporting / Archiving Transcripts", ctx)

    def test_unrelated_bash_does_not_inject_the_cloudflare_skill(self):
        r = run({"command": "cat cloudflare-notes.md"}, tmpdir=self.tmpdir)
        ctx = injected(r)
        if ctx is not None:
            self.assertNotIn("Using Cloudflare API Tokens", ctx)

    def test_unrelated_prompt_does_not_inject_claude_code_log(self):
        r = self._prompt("start a new session please, ako sa mas")
        ctx = self._injected_prompt(r)
        if ctx is not None:
            self.assertNotIn("Browsing / Exporting / Archiving Transcripts", ctx)

    def test_session_word_without_format_does_not_inject_claude_code_log(self):
        # adversarial-review 🟡: the `session` arms MUST require an html/markdown
        # format so a session cookie / session data / "archive this session"
        # no longer spuriously loads the 4.2 KB transcript skill.
        for prompt in [
            "how do I export a session cookie in curl?",
            "export session data to a CSV for analytics",
            "browse the session logs on the server",
            "let's archive this session and move on",
        ]:
            r = self._prompt(prompt, session_id="fp-" + prompt[:12])
            ctx = self._injected_prompt(r)
            if ctx is not None:
                self.assertNotIn("Browsing / Exporting / Archiving Transcripts", ctx, prompt)


class TestCloudflareQuotedTrigger631(TestCase):
    """#631 — the cloudflare row is a RAW_BASH_MATCH_TOPICS row now, so the
    shapes that actually verify a token (api.cloudflare.com inside a QUOTED
    Python/heredoc string, the cfat_ prefix, a `secret request cloudflare*`,
    a read of ~/.secrets/cloudflare*, `webterm-access`) INJECT the skill.

    Root cause the fix removes: strip_carried_text() erased quoted spans +
    heredoc bodies from the Bash haystack BEFORE the cloudflare pattern ran,
    so the one shape most needing the skill (a script calling the API) was
    the one shape that could never match. The owner deleted his master token
    because the skill's verify-endpoint warning never reached the moment of
    decision.
    """

    CF_MARK = "Using Cloudflare API Tokens"  # exists ONLY in the skill body

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _cf(self, command, tool_name="Bash", tool_input=None, session=None):
        ti = tool_input if tool_input is not None else {"command": command}
        session = session or ("cf631-" + command[:8]).replace(" ", "_")
        ctx = injected(run(ti, tool_name=tool_name, session_id=session, tmpdir=self.tmpdir))
        return ctx

    def test_raw_match_topics_set_is_defined_for_cloudflare(self):
        text = HOOK.read_text(encoding="utf-8")
        self.assertIn("RAW_BASH_MATCH_TOPICS", text,
                      "the raw-match mechanism must exist in the injector")
        self.assertIn('"cloudflare-api-tokens"', text,
                      "the cloudflare topic must be a raw-match topic")

    def test_python_quoted_verify_url_injects(self):
        # THE INCIDENT: a script calling api.cloudflare.com from inside a
        # QUOTED Python string. This is the exact shape that caused the loss.
        ctx = self._cf(
            'python3 -c \'import requests; '
            'requests.get("https://api.cloudflare.com/client/v4/user/tokens/verify")\'')
        self.assertIsNotNone(ctx, "the quoted-URL verify script must load the skill")
        self.assertIn(self.CF_MARK, ctx)

    def test_curl_quoted_zones_url_injects(self):
        ctx = self._cf(
            'curl -s -H "Authorization: Bearer $CF_TOKEN" '
            '"https://api.cloudflare.com/client/v4/zones"')
        self.assertIsNotNone(ctx, "a normal quoted curl probe must load the skill")
        self.assertIn(self.CF_MARK, ctx)

    def test_heredoc_python_call_injects(self):
        cmd = (
            "python3 - <<'PY'\n"
            "import requests\n"
            "requests.get('https://api.cloudflare.com/client/v4/zones')\n"
            "PY"
        )
        ctx = self._cf(cmd)
        self.assertIsNotNone(ctx, "a heredoc python call must load the skill")
        self.assertIn(self.CF_MARK, ctx)

    def test_cfat_prefix_injects(self):
        ctx = self._cf(
            'curl -H "Authorization: Bearer cfat_00example001token" https://x.test/')
        self.assertIsNotNone(ctx, "the cfat_ account-token prefix must load the skill")
        self.assertIn(self.CF_MARK, ctx)

    def test_webterm_access_injects(self):
        # webterm-access = Cloudflare Access (Zero Trust) config, #612.
        ctx = self._cf("python3 ~/devel/airuleset/airuleset.py webterm-access --apply")
        self.assertIsNotNone(ctx, "webterm-access is Cloudflare Access config")
        self.assertIn(self.CF_MARK, ctx)

    def test_secret_request_cloudflare_injects(self):
        ctx = self._cf(
            "python3 ~/devel/airuleset/airuleset.py secret request cloudflare_spinbike")
        self.assertIsNotNone(ctx, "requesting a cloudflare secret must load the skill")
        self.assertIn(self.CF_MARK, ctx)

    def test_read_secrets_cloudflare_file_injects(self):
        ctx = self._cf("cat ~/.secrets/cloudflare-spinbike")
        self.assertIsNotNone(ctx, "reading a ~/.secrets/cloudflare* file must load the skill")
        self.assertIn(self.CF_MARK, ctx)

    # --- the raw-match must NOT leak to the other rows ------------------- #

    def test_other_rows_still_use_the_stripped_haystack(self):
        # A git commit MENTIONING `gh pr merge` must NOT inject pr-merge-policy
        # (pr-merge-policy is NOT a raw-match topic — its stripping is intact).
        ctx = self._cf('git commit -m "docs: explain gh pr merge gating"')
        if ctx is not None:
            self.assertNotIn("pr-merge-policy", ctx,
                             "the cloudflare raw-match must not disable the other "
                             "rows' quote-stripping")

    def test_unrelated_cloudflare_filename_does_not_inject(self):
        # A bare "cloudflare" filename (no api.cloudflare.com / cfat_ / secret /
        # ~/.secrets / webterm-access) must NOT inject.
        ctx = self._cf("cat cloudflare-notes.md")
        if ctx is not None:
            self.assertNotIn(self.CF_MARK, ctx)


class TestWiring(TestCase):
    def test_hook_is_wired_on_pretooluse(self):
        conf = json.loads((ROOT / "settings" / "hooks.json").read_text())
        wired = {}
        for entry in conf["hooks"]["PreToolUse"]:
            for h in entry.get("hooks", []):
                if "inject-situational-rule.sh" in h.get("command", ""):
                    wired.setdefault(entry.get("matcher"), 0)
                    wired[entry.get("matcher")] += 1
        self.assertIn("Bash", wired, "not wired for Bash commands")
        self.assertIn("Agent", wired, "not wired for Agent dispatches")

    def test_hook_is_executable_shell(self):
        text = HOOK.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!"), "hook needs a shebang")
        self.assertIn("set -euo pipefail", text)


if __name__ == "__main__":
    main()
