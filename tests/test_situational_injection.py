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
